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

import collections.abc
import email
from email.mime import multipart
from email.mime import text
import os
import pkgutil
import string
from urllib import parse as urlparse

from openstack.config import cloud_region
from openstack import connection
from openstack import exceptions
import os_service_types

from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils
import tenacity

from heat.common import config
from heat.common import exception
from heat.common.i18n import _
from heat.engine.clients import client_plugin
from heat.engine.clients import microversion_mixin
from heat.engine.clients import os as os_client
from heat.engine import constraints
import heat.version

LOG = logging.getLogger(__name__)

CLIENT_NAME = 'openstack'


class OpenStackSDKPlugin(os_client.ExtensionMixin,
                         client_plugin.ClientPlugin):
    """Generic OpenStack SDK client plugin base class.

    Provides common SDK functionality for service-specific client plugins.

    Architecture:
        This class provides GENERIC SDK features:
        - Connection management
        - Network operations
        - Error handling (is_not_found, ignore_not_found)

        Service-specific plugins inherit from this class:
        - NovaSdkClientPlugin (heat.engine.clients.os.nova_sdk.nova)
          * Provides ALL Nova-specific operations including microversion support
          * Registered as 'nova' in setup.cfg
          * Used by resources with default_client_name = 'nova'

    Supported Services:
        - Network (neutron) - Direct use via 'openstack' client
        - Compute (nova) - Via NovaSdkClientPlugin ('nova' client)

    Note: Service-specific features like microversion support should be
    implemented in the respective service plugin (e.g., NovaSdkClientPlugin),
    not in this generic base class.
    """

    exceptions_module = exceptions

    service_types = [NETWORK] = ['network']

    def _create(self, version=None):
        config = cloud_region.from_session(
            # TODO(mordred) The way from_session calculates a cloud name
            # doesn't interact well with the mocks in the test cases. The
            # name is used in logging to distinguish requests made to different
            # clouds. For now, set it to local - but maybe find a way to set
            # it to something more meaningful later.
            name='local',
            session=self.context.keystone_session,
            config=self._get_service_interfaces(),
            region_name=self._get_region_name(),
            app_name='heat',
            app_version=heat.version.version_info.version_string(),
            **self._get_additional_create_args(version))
        return connection.Connection(config=config)

    def _get_additional_create_args(self, version):
        return {}

    def _get_service_interfaces(self):
        interfaces = {}
        types = os_service_types.ServiceTypes()
        for name, _ in config.list_opts():
            if not name or not name.startswith('clients_'):
                continue
            project_name = name.split("_", 1)[0]
            service_data = types.get_service_data_for_project(project_name)
            if not service_data:
                continue
            service_type = service_data['service_type']
            interfaces[service_type + '_interface'] = self._get_client_option(
                service_type, 'endpoint_type')
        return interfaces

    def is_not_found(self, ex):
        """Check if exception is a NotFound error.

        ADAPTED FOR: SDK - handles both NotFoundException and ResourceNotFound
        """
        return isinstance(ex, (exceptions.NotFoundException,
                              exceptions.ResourceNotFound))

    # ============= Network Methods =============

    def find_network_segment(self, value):
        return self.client().network.find_segment(value).id

    def find_network_port(self, value):
        return self.client().network.find_port(value).id

    def find_network_ip(self, value):
        return self.client().network.find_ip(value).id

    # TODO(tkajinam): This should be generalized when we onboard more services
    #                 requiring extension detection.
    @os_client.MEMOIZE_EXTENSIONS
    def _list_extensions(self):
        extensions = self.client().network.extensions()
        return set(extension.alias for extension in extensions)


# ============= Network Constraints =============


class SegmentConstraint(constraints.BaseCustomConstraint):

    expected_exceptions = (exceptions.ResourceNotFound,
                           exceptions.DuplicateResource)

    def validate_with_client(self, client, value):
        sdk_plugin = client.client_plugin(CLIENT_NAME)
        sdk_plugin.find_network_segment(value)
