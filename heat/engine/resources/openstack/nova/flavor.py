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
from heat.engine import translation

LOG = logging.getLogger(__name__)


class NovaFlavor(resource.Resource):
    """A resource for creating OpenStack virtual hardware templates.

    Due to default nova security policy usage of this resource is limited to
    being used by administrators only. The rights may also be delegated to
    other users by redefining the access controls on the nova-api server.

    Note that the current implementation of the Nova Flavor resource does not
    allow specifying the name and flavorid properties for the resource.
    This is done to avoid potential naming collision upon flavor creation as
    all flavor have a global scope.
    """

    support_status = support.SupportStatus(version='2014.2')

    default_client_name = 'nova'

    entity = 'flavors'

    PROPERTIES = (
        TENANTS, ID, NAME, RAM, VCPUS, DISK, SWAP,
        EPHEMERAL, RXTX_FACTOR, EXTRA_SPECS, IS_PUBLIC
    ) = (
        'tenants', 'flavorid', 'name', 'ram', 'vcpus', 'disk', 'swap',
        'ephemeral', 'rxtx_factor', 'extra_specs', 'is_public',
    )

    ATTRIBUTES = (
        IS_PUBLIC_ATTR, EXTRA_SPECS_ATTR
    ) = (
        'is_public', 'extra_specs'
    )

    properties_schema = {
        TENANTS: properties.Schema(
            properties.Schema.LIST,
            _('List of tenants.'),
            update_allowed=True,
            default=[],
            schema=properties.Schema(
                properties.Schema.STRING,
                constraints=[constraints.CustomConstraint('keystone.project')]
            ),
            support_status=support.SupportStatus(version='8.0.0')
        ),
        ID: properties.Schema(
            properties.Schema.STRING,
            _('Unique ID of the flavor. If not specified, '
              'an UUID will be auto generated and used.'),
            support_status=support.SupportStatus(version='7.0.0')
        ),
        NAME: properties.Schema(
            properties.Schema.STRING,
            _('Name of the flavor.'),
            support_status=support.SupportStatus(version='7.0.0'),
        ),
        RAM: properties.Schema(
            properties.Schema.INTEGER,
            _('Memory in MB for the flavor.'),
            required=True
        ),
        VCPUS: properties.Schema(
            properties.Schema.INTEGER,
            _('Number of VCPUs for the flavor.'),
            required=True
        ),
        DISK: properties.Schema(
            properties.Schema.INTEGER,
            _('Size of local disk in GB. The "0" size is a special case that '
              'uses the native base image size as the size of the ephemeral '
              'root volume.'),
            default=0
        ),
        SWAP: properties.Schema(
            properties.Schema.INTEGER,
            _('Swap space in MB.'),
            default=0
        ),
        EPHEMERAL: properties.Schema(
            properties.Schema.INTEGER,
            _('Size of a secondary ephemeral data disk in GB.'),
            default=0
        ),
        RXTX_FACTOR: properties.Schema(
            properties.Schema.NUMBER,
            _('RX/TX factor.'),
            default=1.0
        ),
        EXTRA_SPECS: properties.Schema(
            properties.Schema.MAP,
            _('Key/Value pairs to extend the capabilities of the flavor.'),
            update_allowed=True,
        ),
        IS_PUBLIC: properties.Schema(
            properties.Schema.BOOLEAN,
            _('Scope of flavor accessibility. Public or private. '
              'Default value is True, means public, shared '
              'across all projects.'),
            default=True,
            support_status=support.SupportStatus(version='6.0.0'),
        ),

    }

    attributes_schema = {
        IS_PUBLIC_ATTR: attributes.Schema(
            _('Whether the flavor is shared across all projects.'),
            support_status=support.SupportStatus(version='6.0.0'),
            type=attributes.Schema.BOOLEAN
        ),
        EXTRA_SPECS_ATTR: attributes.Schema(
            _('Extra specs of the flavor in key-value pairs.'),
            support_status=support.SupportStatus(version='7.0.0'),
            type=attributes.Schema.MAP
        )
    }

    def translation_rules(self, properties):
        return [
            translation.TranslationRule(
                properties,
                translation.TranslationRule.RESOLVE,
                [self.TENANTS],
                client_plugin=self.client_plugin('keystone'),
                finder='get_project_id'
            )
        ]

    def handle_create(self):
        args = dict(self.properties)
        if not args['flavorid']:
            args['flavorid'] = 'auto'
        if not args['name']:
            args['name'] = self.physical_resource_name()
        flavor_keys = args.pop(self.EXTRA_SPECS)
        tenants = args.pop(self.TENANTS)
        flavor = self.client().compute.create_flavor(**args)
        self.resource_id_set(flavor.id)
        if flavor_keys:
            self.client().compute.create_flavor_extra_specs(flavor, flavor_keys)

        if not self.properties[self.IS_PUBLIC]:
            if not tenants:
                LOG.info('Tenant property is recommended '
                         'for the private flavors.')
                tenant = self.stack.context.project_id
                # SDK: flavor_add_tenant_access(flavor, tenant)
                self.client().compute.flavor_add_tenant_access(flavor.id, tenant)
            else:
                for tenant in tenants:
                    # SDK: grant access only to the active project(private flavor)
                    self.client().compute.flavor_add_tenant_access(flavor.id, tenant)

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        """Update nova flavor."""
        if self.EXTRA_SPECS in prop_diff:
            flavor = self.client().compute.get_flavor(self.resource_id)
            # SDK: Access extra_specs directly as a dict attribute on Flavor resource
            old_keys = flavor.extra_specs or {}
            for key in old_keys:
                self.client().compute.delete_flavor_extra_specs_property(
                    flavor.id, key)
            new_keys = prop_diff.get(self.EXTRA_SPECS)
            if new_keys is not None:
                self.client().compute.create_flavor_extra_specs(
                    flavor, new_keys)
        """Update tenant access list."""
        if self.TENANTS in prop_diff and not self.properties[self.IS_PUBLIC]:
            # SDK: get_flavor_access() returns FlavorAccess objects with tenant_id
            old_tenants = [
                x.tenant_id for x in
                self.client().compute.get_flavor_access(self.resource_id)
            ] or []
            new_tenants = prop_diff.get(self.TENANTS) or []
            tenants_to_remove = list(set(old_tenants) - set(new_tenants))
            tenants_to_add = list(set(new_tenants) - set(old_tenants))
            if tenants_to_remove or tenants_to_add:
                for _tenant in tenants_to_remove:
                    # SDK: flavor_remove_tenant_access(flavor, tenant)
                    self.client().compute.flavor_remove_tenant_access(
                        self.resource_id, _tenant)
                for _tenant in tenants_to_add:
                    # SDK: flavor_add_tenant_access(flavor, tenant)
                    self.client().compute.flavor_add_tenant_access(
                        self.resource_id, _tenant)

    def _resolve_attribute(self, name):
        if self.resource_id is None:
            return
        flavor = self.client().compute.get_flavor(self.resource_id)
        if name == self.IS_PUBLIC_ATTR:
            return getattr(flavor, name)
        if name == self.EXTRA_SPECS_ATTR:
            # SDK: Access extra_specs directly as a dict attribute on Flavor resource
            return flavor.extra_specs or {}

    def get_live_resource_data(self):
        try:
            flavor = self.client().compute.get_flavor(self.resource_id)
            # SDK: Access extra_specs directly as a dict attribute on Flavor resource
            resource_data = {self.EXTRA_SPECS: flavor.extra_specs or {}}
        except Exception as ex:
            if self.client_plugin().is_not_found(ex):
                raise exception.EntityNotFound(entity='Resource',
                                               name=self.name)
            raise
        return resource_data

    def handle_check(self):
        self.client().compute.get_flavor(self.resource_id)

    def handle_delete(self):
        if self.resource_id is None:
            return
        with self.client_plugin().ignore_not_found:
            self.client().compute.delete_flavor(self.resource_id)

    def parse_live_resource_data(self, resource_properties, resource_data):
        return resource_data


def resource_mapping():
    return {
        'OS::Nova::Flavor': NovaFlavor
    }
