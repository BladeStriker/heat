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

from unittest import mock

from heat.common import exception
from heat.common import template_format
from heat.engine.resources.openstack.nova import server_interface
from heat.tests import common
from heat.tests import utils


server_interface_template_port = '''
heat_template_version: 2021-04-16
resources:
  test_interface:
    type: OS::Nova::ServerInterface
    properties:
      server_id: test-server-id
      port_id: test-port-id
'''

server_interface_template_net = '''
heat_template_version: 2021-04-16
resources:
  test_interface:
    type: OS::Nova::ServerInterface
    properties:
      server_id: test-server-id
      net_id: test-net-id
'''

server_interface_template_net_fixed_ips = '''
heat_template_version: 2021-04-16
resources:
  test_interface:
    type: OS::Nova::ServerInterface
    properties:
      server_id: test-server-id
      net_id: test-net-id
      fixed_ips:
        - 192.168.1.10
'''

server_interface_template_net_multiple_fixed_ips = '''
heat_template_version: 2021-04-16
resources:
  test_interface:
    type: OS::Nova::ServerInterface
    properties:
      server_id: test-server-id
      net_id: test-net-id
      fixed_ips:
        - 192.168.1.10
        - 192.168.1.11
        - 192.168.1.12
'''

server_interface_template_invalid_both = '''
heat_template_version: 2021-04-16
resources:
  test_interface:
    type: OS::Nova::ServerInterface
    properties:
      server_id: test-server-id
      port_id: test-port-id
      net_id: test-net-id
'''

server_interface_template_invalid_neither = '''
heat_template_version: 2021-04-16
resources:
  test_interface:
    type: OS::Nova::ServerInterface
    properties:
      server_id: test-server-id
'''

server_interface_template_invalid_fixed_ips = '''
heat_template_version: 2021-04-16
resources:
  test_interface:
    type: OS::Nova::ServerInterface
    properties:
      server_id: test-server-id
      port_id: test-port-id
      fixed_ips:
        - 192.168.1.10
'''


class ServerInterfaceTest(common.HeatTestCase):
    """Test cases for OS::Nova::ServerInterface resource."""

    def setUp(self):
        super(ServerInterfaceTest, self).setUp()

    def _create_stack(self, tmpl):
        """Helper to create a stack from template string."""
        template = template_format.parse(tmpl)
        stack = utils.parse_stack(template)
        return stack

    def _create_resource(self, stack, resource_name='test_interface'):
        """Helper to get resource from stack."""
        return stack[resource_name]

    def _mock_interface(self, port_id='test-port-id', mac='fa:16:3e:aa:bb:cc',
                        net_id='test-net-id',
                        fixed_ips=None, port_state='ACTIVE'):
        """Create a mock ServerInterface object."""
        if fixed_ips is None:
            fixed_ips = [{'ip_address': '192.168.1.10',
                         'subnet_id': 'test-subnet-id'}]

        mock_iface = mock.Mock()
        mock_iface.port_id = port_id
        mock_iface.mac_addr = mac
        mock_iface.net_id = net_id
        mock_iface.fixed_ips = fixed_ips
        mock_iface.port_state = port_state
        return mock_iface

    def test_interface_create_with_port(self):
        """Test creating interface with existing port."""
        stack = self._create_stack(server_interface_template_port)
        resource = self._create_resource(stack)

        mock_iface = self._mock_interface()
        mock_conn = mock.Mock()
        mock_conn.compute.create_server_interface.return_value = mock_iface

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            resource.handle_create()

        mock_conn.compute.create_server_interface.assert_called_once()
        call_kwargs = mock_conn.compute.create_server_interface.call_args[1]
        self.assertEqual('test-server-id', call_kwargs['server_id'])
        self.assertEqual('test-port-id', call_kwargs['port_id'])

        self.assertEqual('test-port-id', resource.resource_id)

    def test_interface_create_with_net(self):
        """Test creating interface with network (auto-create port)."""
        stack = self._create_stack(server_interface_template_net)
        resource = self._create_resource(stack)

        mock_iface = self._mock_interface(port_id='auto-created-port-id')
        mock_conn = mock.Mock()
        mock_conn.compute.create_server_interface.return_value = mock_iface

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            resource.handle_create()

        mock_conn.compute.create_server_interface.assert_called_once()
        call_kwargs = mock_conn.compute.create_server_interface.call_args[1]
        self.assertEqual('test-server-id', call_kwargs['server_id'])
        self.assertEqual('test-net-id', call_kwargs['net_id'])

        self.assertEqual('auto-created-port-id', resource.resource_id)

    def test_interface_create_with_net_and_fixed_ips(self):
        """Test creating interface with network and specific fixed IPs."""
        stack = self._create_stack(server_interface_template_net_fixed_ips)
        resource = self._create_resource(stack)

        mock_iface = self._mock_interface(port_id='auto-created-port-id')
        mock_conn = mock.Mock()
        mock_conn.compute.create_server_interface.return_value = mock_iface

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            resource.handle_create()

        call_kwargs = mock_conn.compute.create_server_interface.call_args[1]
        self.assertEqual('test-server-id', call_kwargs['server_id'])
        self.assertEqual('test-net-id', call_kwargs['net_id'])
        self.assertIsNotNone(call_kwargs['fixed_ips'])
        self.assertEqual('192.168.1.10',
                        call_kwargs['fixed_ips'][0]['ip_address'])

    def test_interface_create_with_multiple_fixed_ips(self):
        """Test creating interface with multiple fixed IP addresses."""
        stack = self._create_stack(
            server_interface_template_net_multiple_fixed_ips)
        resource = self._create_resource(stack)

        mock_iface = self._mock_interface(port_id='auto-created-port-id')
        mock_conn = mock.Mock()
        mock_conn.compute.create_server_interface.return_value = mock_iface

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            resource.handle_create()

        call_kwargs = mock_conn.compute.create_server_interface.call_args[1]
        self.assertEqual('test-server-id', call_kwargs['server_id'])
        self.assertEqual('test-net-id', call_kwargs['net_id'])
        self.assertIsNotNone(call_kwargs['fixed_ips'])
        self.assertEqual(3, len(call_kwargs['fixed_ips']))
        self.assertEqual('192.168.1.10',
                        call_kwargs['fixed_ips'][0]['ip_address'])
        self.assertEqual('192.168.1.11',
                        call_kwargs['fixed_ips'][1]['ip_address'])
        self.assertEqual('192.168.1.12',
                        call_kwargs['fixed_ips'][2]['ip_address'])

    def test_interface_delete(self):
        """Test deleting (detaching) interface."""
        stack = self._create_stack(server_interface_template_port)
        resource = self._create_resource(stack)

        resource.resource_id = 'test-port-id'

        mock_conn = mock.Mock()

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            resource.handle_delete()

        mock_conn.compute.delete_server_interface.assert_called_once_with(
            'test-port-id', 'test-server-id', ignore_missing=True
        )

    def test_interface_delete_not_found(self):
        """Test deleting interface that's already gone."""
        stack = self._create_stack(server_interface_template_port)
        resource = self._create_resource(stack)
        resource.resource_id = 'test-port-id'

        mock_conn = mock.Mock()

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            resource.handle_delete()

        mock_conn.compute.delete_server_interface.assert_called_once_with(
            'test-port-id', 'test-server-id', ignore_missing=True
        )

    def test_interface_delete_no_resource_id(self):
        """Test deleting interface with no resource_id."""
        stack = self._create_stack(server_interface_template_port)
        resource = self._create_resource(stack)
        resource.resource_id = None

        mock_conn = mock.Mock()

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            resource.handle_delete()

        mock_conn.compute.delete_server_interface.assert_not_called()

    def test_interface_attributes(self):
        """Test resolving interface attributes."""
        stack = self._create_stack(server_interface_template_port)
        resource = self._create_resource(stack)
        resource.resource_id = 'test-port-id'

        mock_iface = self._mock_interface(
            port_id='test-port-id',
            mac='fa:16:3e:11:22:33',
            net_id='test-net-id',
            fixed_ips=[{'ip_address': '192.168.1.20'}],
            port_state='ACTIVE'
        )
        mock_conn = mock.Mock()
        mock_conn.compute.get_server_interface.return_value = mock_iface

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            self.assertEqual('fa:16:3e:11:22:33',
                           resource._resolve_attribute('mac_addr'))
            self.assertEqual('ACTIVE',
                           resource._resolve_attribute('port_state'))

    def test_interface_attributes_no_resource_id(self):
        """Test attributes return None when resource_id is not set."""
        stack = self._create_stack(server_interface_template_port)
        resource = self._create_resource(stack)
        resource.resource_id = None

        self.assertIsNone(resource._resolve_attribute('mac_addr'))

    def test_interface_attributes_not_found(self):
        """Test attributes return None when interface not found."""
        stack = self._create_stack(server_interface_template_port)
        resource = self._create_resource(stack)
        resource.resource_id = 'test-port-id'

        mock_conn = mock.Mock()
        mock_conn.compute.get_server_interface.return_value = None

        with mock.patch.object(resource, 'client', return_value=mock_conn):
            self.assertIsNone(resource._resolve_attribute('mac_addr'))

    @mock.patch('heat.engine.clients.os.nova.NovaClientPlugin.get_server')
    @mock.patch('heat.engine.clients.os.neutron.NeutronClientPlugin.find_resourceid_by_name_or_id')
    def test_validate_port_and_net_exclusive(self, mock_find, mock_server):
        """Test validation fails when both port_id and net_id specified."""
        mock_server.return_value = mock.Mock(id='server-id')
        mock_find.return_value = 'resolved-id'

        stack = self._create_stack(server_interface_template_invalid_both)
        resource = self._create_resource(stack)

        exc = self.assertRaises(exception.StackValidationFailed,
                               resource.validate)
        self.assertIn('mutually exclusive', str(exc))

    @mock.patch('heat.engine.clients.os.nova.NovaClientPlugin.get_server')
    def test_validate_port_or_net_required(self, mock_server):
        """Test validation fails when neither port_id nor net_id specified."""
        mock_server.return_value = mock.Mock(id='server-id')

        stack = self._create_stack(server_interface_template_invalid_neither)
        resource = self._create_resource(stack)

        exc = self.assertRaises(exception.StackValidationFailed,
                               resource.validate)
        self.assertIn('must be specified', str(exc))

    @mock.patch('heat.engine.clients.os.nova.NovaClientPlugin.get_server')
    @mock.patch('heat.engine.clients.os.neutron.NeutronClientPlugin.find_resourceid_by_name_or_id')
    def test_validate_fixed_ips_requires_net(self, mock_find, mock_server):
        """Test validation fails when fixed_ips used without net_id."""
        mock_server.return_value = mock.Mock(id='server-id')
        mock_find.return_value = 'resolved-id'

        stack = self._create_stack(
            server_interface_template_invalid_fixed_ips)
        resource = self._create_resource(stack)

        exc = self.assertRaises(exception.StackValidationFailed,
                               resource.validate)
        self.assertIn('only valid when "net_id"', str(exc))

    def test_properties_immutable(self):
        """Test that properties have update_allowed=False."""
        stack = self._create_stack(server_interface_template_port)
        resource = self._create_resource(stack)

        schema = resource.properties_schema
        self.assertFalse(schema['server_id'].update_allowed)
        self.assertFalse(schema['port_id'].update_allowed)
        self.assertFalse(schema['net_id'].update_allowed)
        self.assertFalse(schema['fixed_ips'].update_allowed)
