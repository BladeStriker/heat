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

from oslo_log import log as logging

from heat.common import exception
from heat.common.i18n import _
from heat.engine import attributes
from heat.engine import constraints
from heat.engine import properties
from heat.engine import resource
from heat.engine import support

LOG = logging.getLogger(__name__)


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
        SERVER, PORT, NET, FIXED_IPS,
    ) = (
        'server_id', 'port_id', 'net_id', 'fixed_ips',
    )

    ATTRIBUTES = (
        PORT_ID, MAC_ADDR, NET_ID, FIXED_IPS_ATTR, PORT_STATE,
    ) = (
        'port_id', 'mac_addr', 'net_id', 'fixed_ips', 'port_state',
    )

    properties_schema = {
        SERVER: properties.Schema(
            properties.Schema.STRING,
            _('The ID or name of the server to which the interface attaches.'),
            required=True,
            update_allowed=False,
            constraints=[
                constraints.CustomConstraint('nova.server')
            ]
        ),
        PORT: properties.Schema(
            properties.Schema.STRING,
            _('The ID or name of the port to attach to the server. '
              'Mutually exclusive with net_id.'),
            constraints=[
                constraints.CustomConstraint('neutron.port')
            ]
        ),
        NET: properties.Schema(
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
                properties.Schema.MAP,
                schema={
                    'ip_address': properties.Schema(
                        properties.Schema.STRING,
                        _('Fixed IP address.')
                    )
                }
            )
        ),
    }

    attributes_schema = {
        PORT_ID: attributes.Schema(
            _('The ID of the attached port.'),
            type=attributes.Schema.STRING
        ),
        MAC_ADDR: attributes.Schema(
            _('The MAC address of the attached interface.'),
            type=attributes.Schema.STRING
        ),
        NET_ID: attributes.Schema(
            _('The network ID of the attached interface.'),
            type=attributes.Schema.STRING
        ),
        FIXED_IPS_ATTR: attributes.Schema(
            _('Fixed IP addresses of the attached interface.'),
            type=attributes.Schema.LIST
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

        port_id = self.properties[self.PORT]
        net_id = self.properties[self.NET]
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
        server_id = self.properties[self.SERVER]
        port_id = self.properties[self.PORT]
        net_id = self.properties[self.NET]
        fixed_ips = self.properties[self.FIXED_IPS]

        conn = self.client()

        attrs = {'server_id': server_id}
        if port_id:
            attrs['port_id'] = port_id
        elif net_id:
            attrs['net_id'] = net_id
            if fixed_ips:
                attrs['fixed_ips'] = fixed_ips

        interface = conn.compute.create_server_interface(**attrs)

        if not interface:
            raise exception.ResourceFailure(
                exception.Error(
                    _('Failed to attach interface to server %s') % server_id
                ),
                self
            )

        self.resource_id_set(interface.port_id)

        LOG.debug('Attached interface %s to server %s',
                  interface.port_id, server_id)

    def check_create_complete(self, *args):
        """Check if interface attachment is complete.

        OpenstackSDK operations are synchronous, so this always returns True.
        """
        return True

    def handle_delete(self):
        """Detach the interface from the server."""
        if self.resource_id is None:
            return

        server_id = self.properties[self.SERVER]
        port_id = self.resource_id

        conn = self.client()

        conn.compute.delete_server_interface(
            port_id,
            server_id,
            ignore_missing=True
        )

        LOG.debug('Detached interface %s from server %s',
                  port_id, server_id)

    def check_delete_complete(self, *args):
        """Check if interface detachment is complete.

        OpenstackSDK operations are synchronous, so this always returns True.
        """
        return True

    def _resolve_attribute(self, name):
        """Resolve attribute values for the interface."""
        if self.resource_id is None:
            return None

        server_id = self.properties[self.SERVER]
        port_id = self.resource_id

        try:
            conn = self.client()
            interface = conn.compute.get_server_interface(port_id, server_id)

            if not interface:
                return None

            if name == self.PORT_ID:
                return interface.port_id
            elif name == self.MAC_ADDR:
                return interface.mac_addr
            elif name == self.NET_ID:
                return interface.net_id
            elif name == self.FIXED_IPS_ATTR:
                return interface.fixed_ips
            elif name == self.PORT_STATE:
                return interface.port_state

        except Exception as ex:
            LOG.warning('Failed to resolve attribute %s for interface %s: %s',
                        name, port_id, ex)
            return None

    def get_reference_id(self):
        """Return the port_id as the reference ID."""
        return self.resource_id


def resource_mapping():
    return {
        'OS::Nova::ServerInterface': ServerInterface,
    }
