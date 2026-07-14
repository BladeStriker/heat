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

"""Nova-specific resource constraints.

These constraints validate Nova resources (flavors, servers, keypairs, hosts)
using the NovaSdkClientPlugin.

All constraints use resource_client_name = 'nova' which automatically loads
the NovaSdkClientPlugin (SDK-based) instead of the old NovaClientPlugin.
"""

from openstack import exceptions as sdk_exceptions

from heat.common import exception as heat_exception
from heat.engine import constraints


# Client name that these constraints use
CLIENT_NAME = 'nova'


class NovaBaseConstraint(constraints.BaseCustomConstraint):
    """Base class for Nova constraints.

    All Nova constraints use the 'nova' client plugin, which resolves to
    NovaSdkClientPlugin (SDK-based, from nova_sdk module).

    This ensures all Nova resource validation uses OpenStack SDK instead
    of the deprecated python-novaclient.

    CRITICAL: expected_exceptions must be SPECIFIC, not broad:
    - heat_exception.EntityNotFound: Raised by nova_sdk plugin methods
    - sdk_exceptions.ResourceNotFound: Raised directly by SDK

    DO NOT use (Exception,) as it would swallow ALL errors including:
    - Syntax bugs, auth failures, database disconnects, timeouts
    - These would incorrectly show as "Resource not found" to users
    """
    resource_client_name = CLIENT_NAME
    expected_exceptions = (heat_exception.EntityNotFound,
                          sdk_exceptions.ResourceNotFound)


class ServerConstraint(NovaBaseConstraint):
    """Constraint for Nova server resources.

    Validates that a server exists and is accessible using the nova plugin.

    Example usage in resource schema:
        properties_schema = {
            'server_id': properties.Schema(
                properties.Schema.STRING,
                constraints=[constraints.CustomConstraint('nova.server')]
            )
        }
    """
    resource_getter_name = 'get_server'


class FlavorConstraint(NovaBaseConstraint):
    """Constraint for Nova flavor resources.

    Validates that a flavor exists and is accessible using the nova plugin.

    Example usage:
        properties_schema = {
            'flavor': properties.Schema(
                properties.Schema.STRING,
                constraints=[constraints.CustomConstraint('nova.flavor')]
            )
        }
    """
    resource_getter_name = 'get_flavor'


class KeypairConstraint(NovaBaseConstraint):
    """Constraint for Nova keypair resources.

    Validates that a keypair exists and is accessible using the nova plugin.

    Example usage:
        properties_schema = {
            'key_name': properties.Schema(
                properties.Schema.STRING,
                constraints=[constraints.CustomConstraint('nova.keypair')]
            )
        }
    """
    resource_getter_name = 'get_keypair'


class HostConstraint(NovaBaseConstraint):
    """Constraint for Nova host/hypervisor resources.

    Validates that a hypervisor host exists and is accessible using the nova plugin.

    Example usage:
        properties_schema = {
            'host': properties.Schema(
                properties.Schema.STRING,
                constraints=[constraints.CustomConstraint('nova.host')]
            )
        }
    """
    resource_getter_name = 'get_host'
