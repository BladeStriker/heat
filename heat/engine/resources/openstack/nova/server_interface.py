#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from heat.common import exception
from heat.common.i18n import _
from heat.engine import attributes
from heat.engine import constraints
from heat.engine import properties
from heat.engine import resource
from heat.engine import support


class ServerInterface(resource.Resource):
    """A resource for attaching network interfaces to Nova servers.

    This resource allows attaching network interfaces to existing Nova
    servers. It supports two modes:

    1. Port-based: Attach an existing Neutron port to the server
    2. Network-based: Specify a network and let Nova create a port
       automatically
    """

    support_status = support.SupportStatus(version='26.0.0')

    PROPERTIES = (
        SERVER_ID, PORT_ID, NET_ID, FIXED_IPS,
    ) = (
        'server_id', 'port_id', 'net_id', 'fixed_ips',
    )

    ATTRIBUTES = (
        MAC_ADDR, PORT_STATE,
    ) = (
        'mac_addr', 'port_state',
    )

    properties_schema = {
        SERVER_ID: properties.Schema(
            properties.Schema.STRING,
            _('The ID or name of the server to which the interface attaches.'),
            required=True,
            constraints=[
                constraints.CustomConstraint('nova.server')
            ]
        ),
        PORT_ID: properties.Schema(
            properties.Schema.STRING,
            _('The ID or name of the port to attach to the server. '
              'Mutually exclusive with net_id.'),
            constraints=[
                constraints.CustomConstraint('neutron.port')
            ]
        ),
        NET_ID: properties.Schema(
            properties.Schema.STRING,
            _('The ID or name of the network. Nova will create a port '
              'on this network automatically. Mutually exclusive with port_id.'),
            constraints=[
                constraints.CustomConstraint('neutron.network')
            ]
        ),
        FIXED_IPS: properties.Schema(
            properties.Schema.LIST,
            _('Fixed IP addresses to assign when using net_id. '
              'Only valid with net_id, not with port_id.'),
            schema=properties.Schema(
                properties.Schema.STRING,
                _('Fixed IP address.'),
                constraints=[
                    constraints.CustomConstraint('ip_addr')
                ]
            )
        ),
    }

    attributes_schema = {
        MAC_ADDR: attributes.Schema(
            _('The MAC address of the attached interface.'),
            type=attributes.Schema.STRING
        ),
        PORT_STATE: attributes.Schema(
            _('The port state of the attached interface.'),
            type=attributes.Schema.STRING
        ),
    }

    default_client_name = 'openstack'

    def validate(self):
        """Validate the provided properties."""
        super(ServerInterface, self).validate()

        port_id = self.properties[self.PORT_ID]
        net_id = self.properties[self.NET_ID]
        fixed_ips = self.properties[self.FIXED_IPS]

        if port_id and net_id:
            msg = _('Properties "port_id" and "net_id" are mutually '
                    'exclusive.')
            raise exception.StackValidationFailed(message=msg)

        if not port_id and not net_id:
            msg = _('Either "port_id" or "net_id" must be specified.')
            raise exception.StackValidationFailed(message=msg)

        if fixed_ips and not net_id:
            msg = _('Property "fixed_ips" is only valid when "net_id" '
                    'is specified.')
            raise exception.StackValidationFailed(message=msg)

    def handle_create(self):
        """Attach the interface to the server."""
        attrs = {'server_id': self.properties[self.SERVER_ID]}

        if self.properties[self.PORT_ID]:
            attrs['port_id'] = self.properties[self.PORT_ID]
        elif self.properties[self.NET_ID]:
            attrs['net_id'] = self.properties[self.NET_ID]
            if self.properties[self.FIXED_IPS]:
                attrs['fixed_ips'] = [
                    {'ip_address': ip} for ip in self.properties[self.FIXED_IPS]
                ]

        interface = self.client().compute.create_server_interface(**attrs)
        self.resource_id_set(interface.port_id)

    def handle_delete(self):
        """Detach the interface from the server."""
        if self.resource_id is None:
            return

        self.client().compute.delete_server_interface(
            self.resource_id,
            self.properties[self.SERVER_ID],
            ignore_missing=True
        )

    def _resolve_attribute(self, name):
        """Resolve attribute values for the interface."""
        if self.resource_id is None:
            return None

        interface = self.client().compute.get_server_interface(
            self.resource_id,
            self.properties[self.SERVER_ID]
        )

        if not interface:
            return None

        if name == self.MAC_ADDR:
            return interface.mac_addr
        elif name == self.PORT_STATE:
            return interface.port_state


def resource_mapping():
    return {
        'OS::Nova::ServerInterface': ServerInterface,
    }
