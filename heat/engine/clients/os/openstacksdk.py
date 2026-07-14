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
                         microversion_mixin.MicroversionMixin,
                         client_plugin.ClientPlugin):
    """OpenStack SDK client plugin.

    Provides access to OpenStack services via openstacksdk library.

    Currently supports:
    - Network (neutron) - Full support
    - Compute (nova) - Phase 1 migration complete
      * Resources: Keypair, Flavor, HostAggregate, Quota
      * Constraints: All migrated from nova.py
      * Pattern: Resources use default_client_name='openstack' and call
                 SDK methods via self.client().compute.METHOD()

    Phase 2 (Future): Server, ServerGroup, FloatingIP migration
    """

    exceptions_module = exceptions

    service_types = [NETWORK, COMPUTE] = ['network', 'compute']

    # Compute API configuration
    # MIGRATED FROM: nova.py lines 64-66
    COMPUTE_API_VERSION = '2.1'
    max_microversion = cfg.CONF.max_nova_api_microversion

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
        """Check if exception is a NotFound error (network or compute).

        ADAPTED FOR: SDK - handles both NotFoundException and ResourceNotFound
        """
        return isinstance(ex, (exceptions.NotFoundException,
                              exceptions.ResourceNotFound))

    # ============= Microversion Support =============
    # MIGRATED FROM: nova.py lines 70-112

    def _get_service_name(self):
        """Return service name for microversion support.

        REQUIRED BY: MicroversionMixin
        """
        return self.COMPUTE

    def get_max_microversion(self):
        """Get maximum supported compute API microversion.

        MIGRATED FROM: nova.py lines 96-106
        ADAPTED FOR: SDK - simplified, no novaclient.API_MAX_VERSION cap needed
        """
        if not self.max_microversion:
            # SDK doesn't need novaclient's API_MAX_VERSION cap
            configured = cfg.CONF.max_nova_api_microversion
            if configured:
                self.max_microversion = configured
            else:
                self.max_microversion = self.COMPUTE_API_VERSION
        return self.max_microversion

    def is_version_supported(self, version):
        """Check if a microversion is supported by the compute service.

        MIGRATED FROM: nova.py lines 108-112
        ADAPTED FOR: SDK - uses oslo_utils.versionutils
        """
        from oslo_utils import versionutils
        return versionutils.is_compatible(version,
                                         self.get_max_microversion())

    # ============= Flavor Constraint Helpers =============
    # MIGRATED FROM: nova.py lines 263-289

    def find_flavor_by_name_or_id(self, flavor):
        """Find the specified flavor by name or id.

        MIGRATED FROM: nova.py lines 263-270
        USED BY: FlavorConstraint
        """
        return self._find_flavor_id(self.context.project_id, flavor)

    @os_client.MEMOIZE_FINDER
    def _find_flavor_id(self, tenant_id, flavor):
        """Cached flavor lookup.

        MIGRATED FROM: nova.py lines 272-276
        UNCHANGED: Tenant id in signature is for memoization key
        """
        return self.get_flavor(flavor).id

    def get_flavor(self, flavor_identifier):
        """Get the flavor object for the specified flavor name or id.

        MIGRATED FROM: nova.py lines 278-289
        ADAPTED FOR: SDK - uses compute.find_flavor()

        :param flavor_identifier: the name or id of the flavor to find
        :returns: a flavor object with name or id :flavor:
        """
        try:
            # SDK's find_flavor does name-or-id lookup automatically
            flavor = self.client().compute.find_flavor(
                flavor_identifier, ignore_missing=False)
        except exceptions.ResourceNotFound:
            raise exception.EntityNotFound(entity='Flavor',
                                          name=flavor_identifier)
        return flavor

    # ============= Hypervisor Constraint Helper =============
    # MIGRATED FROM: nova.py lines 291-299

    def get_host(self, hypervisor_hostname):
        """Get list of matching hypervisors by specified name.

        MIGRATED FROM: nova.py lines 291-299
        ADAPTED FOR: SDK - uses compute.hypervisors()
        USED BY: HostConstraint

        :param hypervisor_hostname: the name of host to find
        :returns: list of matching hypervisor hosts
        :raises: ResourceNotFound if no hypervisors match
        """
        hypervisors = list(self.client().compute.hypervisors(
            hypervisor_hostname_pattern=hypervisor_hostname))
        if not hypervisors:
            raise exceptions.ResourceNotFound(
                f"Hypervisor {hypervisor_hostname} not found")
        return hypervisors

    # ============= Keypair Constraint Helper =============
    # MIGRATED FROM: nova.py lines 301-311

    def get_keypair(self, key_name):
        """Get the public key specified by key_name.

        MIGRATED FROM: nova.py lines 301-311
        ADAPTED FOR: SDK - uses compute.find_keypair()
        USED BY: KeypairConstraint

        :param key_name: the name of the key to look for
        :returns: the keypair (name, public_key) for :key_name:
        :raises: EntityNotFound if keypair not found
        """
        try:
            return self.client().compute.find_keypair(
                key_name, ignore_missing=False)
        except exceptions.ResourceNotFound:
            raise exception.EntityNotFound(entity='Key', name=key_name)

    # ============= Server Constraint Helper =============
    # MIGRATED FROM: nova.py lines 133-148

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(
            max(cfg.CONF.client_retry_limit + 1, 0)),
        retry=tenacity.retry_if_exception(
            client_plugin.retry_if_connection_err),
        reraise=True)
    def get_server(self, server):
        """Return fresh server object.

        MIGRATED FROM: nova.py lines 159-168
        ADAPTED FOR: SDK - uses compute.get_server()
        USED BY: ServerConstraint and server.py

        Substitutes SDK's ResourceNotFound for Heat's EntityNotFound,
        to be returned to user as HTTP error.

        :param server: server id or name
        :returns: server object
        :raises: EntityNotFound if server not found
        """
        try:
            return self.client().compute.get_server(server)
        except exceptions.ResourceNotFound:
            raise exception.EntityNotFound(entity='Server', name=server)

    def fetch_server(self, server_id):
        """Fetch fresh server object from compute service.

        MIGRATED FROM: nova.py lines 170-194
        ADAPTED FOR: SDK - uses compute.get_server()
        USED BY: server.py various check_*_complete methods

        Log warnings and return None for non-critical API errors.
        Use this method in various ``check_*_complete`` resource methods,
        where intermittent errors can be tolerated.

        :param server_id: server ID
        :returns: server object or None if tolerable error occurred
        """
        server = None
        try:
            server = self.client().compute.get_server(server_id)
        except exceptions.HttpException as exc:
            # SDK uses HttpException with status_code attribute
            if exc.status_code == 429:  # Over limit
                LOG.warning("Received an OverLimit response when "
                            "fetching server (%(id)s) : %(exception)s",
                            {'id': server_id, 'exception': exc})
            elif exc.status_code in (500, 503):
                LOG.warning("Received the following exception when "
                            "fetching server (%(id)s) : %(exception)s",
                            {'id': server_id, 'exception': exc})
            else:
                raise
        except exceptions.SDKException as exc:
            LOG.warning("Exception fetching server (%(id)s): %(exception)s",
                        {'id': server_id, 'exception': exc})
        return server

    def get_status(self, server):
        """Return the server's status.

        MIGRATED FROM: nova.py lines 233-240
        ADAPTED FOR: SDK
        USED BY: server.py

        :param server: server object
        :returns: server status string (stripped of extra info)
        """
        status = getattr(server, 'status', None)
        if status:
            # Some clouds append extra (STATUS) strings, strip them
            status = status.split('(')[0]
        return status

    def _check_active(self, server, res_name='Server'):
        """Check server status.

        MIGRATED FROM: nova.py lines 242-281
        ADAPTED FOR: SDK
        USED BY: server.py check_create_complete and related methods

        Accepts both server IDs and server objects.
        Returns True if server is ACTIVE,
        raises errors when server has an ERROR or unknown to Heat status,
        returns False otherwise.

        :param server: server ID or server object
        :param res_name: name of the resource to use in exception messages
        :returns: True if ACTIVE, False if still processing
        :raises: exception.ResourceInError if server is in ERROR state
        :raises: exception.ResourceUnknownStatus for unexpected states
        """
        # Deferred statuses - server is still building/processing
        deferred_statuses = frozenset(['BUILD', 'HARD_REBOOT', 'RESIZE',
                                       'VERIFY_RESIZE', 'REVERT_RESIZE'])

        # If server is an ID, fetch the object
        if isinstance(server, str):
            server = self.fetch_server(server)
            if server is None:
                return False
            status = self.get_status(server)
        else:
            status = self.get_status(server)
            # For server objects, refresh if not ACTIVE
            if status != 'ACTIVE':
                try:
                    server = self.client().compute.get_server(server.id)
                    status = self.get_status(server)
                except exceptions.SDKException:
                    return False

        if status in deferred_statuses:
            return False
        elif status == 'ACTIVE':
            return True
        elif status == 'ERROR':
            fault = getattr(server, 'fault', {})
            raise exception.ResourceInError(
                resource_status=status,
                status_reason=_("Message: %(message)s, Code: %(code)s") % {
                    'message': fault.get('message', _('Unknown')),
                    'code': fault.get('code', _('Unknown'))
                })
        else:
            raise exception.ResourceUnknownStatus(
                resource_status=server.status,
                result=_('%s is not active') % res_name)

    def meta_serialize(self, metadata):
        """Serialize non-string metadata values before sending to compute service.

        MIGRATED FROM: nova.py lines 624-633
        ADAPTED FOR: SDK
        USED BY: server.py

        :param metadata: metadata dictionary
        :returns: serialized metadata dict with JSON-encoded non-string values
        :raises: exception.StackValidationFailed if metadata is not a map
        """
        if not isinstance(metadata, collections.abc.Mapping):
            raise exception.StackValidationFailed(message=_(
                "server metadata needs to be a Map."))

        return dict((key, (value if isinstance(value, str)
                          else jsonutils.dumps(value))
                     ) for (key, value) in metadata.items())

    def meta_update(self, server, metadata):
        """Delete/Add metadata for a server as needed.

        MIGRATED FROM: nova.py lines 635-644
        ADAPTED FOR: SDK - uses compute.set_server_metadata and delete_server_metadata
        USED BY: server.py handle_update

        :param server: server object
        :param metadata: new metadata dictionary
        """
        metadata = self.meta_serialize(metadata)
        current_md = server.metadata or {}
        to_del = sorted(set(current_md) - set(metadata))

        client = self.client().compute
        if len(to_del) > 0:
            client.delete_server_metadata(server, to_del)

        client.set_server_metadata(server, metadata)

    def resize(self, server_id, flavor_id):
        """Resize the server.

        MIGRATED FROM: nova.py lines 540-547
        ADAPTED FOR: SDK - uses compute.resize_server
        USED BY: server.py _update_flavor

        :param server_id: server ID
        :param flavor_id: new flavor ID
        :returns: True if resize initiated, False if server not found
        """
        server = self.fetch_server(server_id)
        if server:
            self.client().compute.resize_server(server, flavor_id)
            return True
        else:
            return False

    def check_resize(self, server_id, flavor):
        """Verify that a resizing server reached VERIFY_RESIZE status.

        MIGRATED FROM: nova.py lines 549-565
        ADAPTED FOR: SDK
        USED BY: server.py check_update_complete

        :param server_id: server ID
        :param flavor: target flavor name/ID (for error messages)
        :returns: True if VERIFY_RESIZE, False if still resizing
        :raises: exception.Error if resize failed
        """
        server = self.fetch_server(server_id)
        # Resize is async - server may stay ACTIVE or go to RESIZE before VERIFY_RESIZE
        if not server or server.status in ('RESIZE', 'ACTIVE'):
            return False
        if server.status == 'VERIFY_RESIZE':
            return True
        else:
            raise exception.Error(
                _("Resizing to '%(flavor)s' failed, status '%(status)s'") %
                dict(flavor=flavor, status=server.status))

    def verify_resize(self, server_id):
        """Confirm server resize.

        MIGRATED FROM: nova.py lines 567-578
        ADAPTED FOR: SDK - uses compute.confirm_server_resize
        USED BY: server.py check_update_complete

        :param server_id: server ID
        :returns: True if confirmed, False if not in VERIFY_RESIZE state
        :raises: exception.ResourceUnknownStatus if not in VERIFY_RESIZE
        """
        server = self.fetch_server(server_id)
        if not server:
            return False
        status = self.get_status(server)
        if status == 'VERIFY_RESIZE':
            self.client().compute.confirm_server_resize(server)
            return True
        else:
            msg = _("Could not confirm resize of server %s") % server_id
            raise exception.ResourceUnknownStatus(
                result=msg, resource_status=status)

    def check_verify_resize(self, server_id):
        """Wait for resize confirmation to complete.

        MIGRATED FROM: nova.py lines 580-596
        ADAPTED FOR: SDK
        USED BY: server.py check_update_complete

        :param server_id: server ID
        :returns: True if ACTIVE, False if still confirming
        :raises: exception.ResourceUnknownStatus if failed
        """
        server = self.fetch_server(server_id)
        if not server:
            return False
        status = self.get_status(server)
        if status == 'ACTIVE':
            return True
        if status == 'VERIFY_RESIZE':
            return False
        # Wait for any resize tasks to finish
        task_state = getattr(server, 'OS-EXT-STS:task_state', None)
        if task_state is not None and 'resize' in task_state:
            return False
        else:
            msg = _("Confirm resize for server %s failed") % server_id
            raise exception.ResourceUnknownStatus(
                result=msg, resource_status=status)

    def rebuild(self, server_id, image_id, password=None,
                preserve_ephemeral=False, meta=None, files=None):
        """Rebuild the server.

        MIGRATED FROM: nova.py lines 598-608
        ADAPTED FOR: SDK - uses compute.rebuild_server
        USED BY: server.py _update_image

        :param server_id: server ID
        :param image_id: new image ID
        :param password: admin password (optional)
        :param preserve_ephemeral: preserve ephemeral disk (default False)
        :param meta: metadata dict (optional)
        :param files: personality files dict (optional)
        :returns: True if rebuild initiated, False if server not found
        """
        server = self.fetch_server(server_id)
        if server:
            self.client().compute.rebuild_server(
                server, image_id,
                admin_password=password,
                preserve_ephemeral=preserve_ephemeral,
                metadata=meta,
                files=files)
            return True
        else:
            return False

    def check_rebuild(self, server_id):
        """Verify that a rebuilding server is rebuilt.

        MIGRATED FROM: nova.py lines 610-622
        ADAPTED FOR: SDK
        USED BY: server.py check_update_complete

        :param server_id: server ID
        :returns: True if rebuild complete, False if still rebuilding
        :raises: exception.Error if rebuild failed
        """
        server = self.fetch_server(server_id)
        if server is None or server.status == 'REBUILD':
            return False
        if server.status == 'ERROR':
            raise exception.Error(
                _("Rebuilding server failed, status '%s'") % server.status)
        else:
            return True

    def check_delete_server_complete(self, server_id):
        """Wait for server to disappear from compute service.

        MIGRATED FROM: nova.py lines 504-534
        ADAPTED FOR: SDK
        USED BY: server.py check_delete_complete

        :param server_id: server ID
        :returns: True if deleted, False if still deleting
        :raises: exception.ResourceInError if deletion failed
        """
        try:
            server = self.fetch_server(server_id)
        except exceptions.ResourceNotFound:
            return True
        except Exception as exc:
            # Ignore not found errors
            if isinstance(exc, exceptions.NotFoundException):
                return True
            # Other exceptions need investigation
            LOG.warning("Error fetching server %(id)s during delete: %(exc)s",
                        {'id': server_id, 'exc': exc})
            return False

        if not server:
            return True

        # Check task state - server status won't change until delete task completes
        task_state = getattr(server, 'OS-EXT-STS:task_state', None)
        if task_state == 'deleting':
            return False

        status = self.get_status(server)
        if status == 'DELETED':
            return True

        if status == 'SOFT_DELETED':
            # Force delete soft-deleted servers
            self.client().compute.force_delete_server(server_id)
            return False
        elif status == 'ERROR':
            fault = getattr(server, 'fault', {})
            message = fault.get('message', 'Unknown')
            code = fault.get('code')
            errmsg = _("Server %(name)s delete failed: (%(code)s) "
                       "%(message)s") % dict(name=server.name,
                                             code=code,
                                             message=message)
            raise exception.ResourceInError(resource_status=status,
                                            status_reason=errmsg)
        return False

    def rename(self, server, name):
        """Update the name for a server.

        MIGRATED FROM: nova.py lines 536-538
        ADAPTED FOR: SDK - uses compute.update_server
        USED BY: server.py handle_update

        :param server: server object or ID
        :param name: new server name
        """
        self.client().compute.update_server(server, name=name)

    @staticmethod
    def is_ignition_format(userdata):
        """Check if userdata is in Ignition format (CoreOS).

        MIGRATED FROM: nova.py lines 459-466
        NO CHANGES: Static method, SDK-independent
        USED BY: build_userdata

        :param userdata: user data string
        :returns: True if Ignition format, False otherwise
        """
        try:
            payload = jsonutils.loads(userdata)
            ig = payload.get("ignition")
            return True if ig and ig.get("version") else False
        except Exception:
            return False

    @staticmethod
    def build_ignition_data(metadata, userdata):
        """Build Ignition-format userdata with metadata injection.

        MIGRATED FROM: nova.py lines 468-502
        NO CHANGES: Static method, SDK-independent
        USED BY: build_userdata

        :param metadata: metadata dict to inject
        :param userdata: base Ignition userdata
        :returns: updated Ignition JSON string
        """
        if not metadata:
            return userdata

        payload = jsonutils.loads(userdata)
        encoded_metadata = urlparse.quote(jsonutils.dumps(metadata))
        path_list = ["/var/lib/heat-cfntools/cfn-init-data",
                     "/var/lib/cloud/data/cfn-init-data"]
        ignition_format_metadata = {
            "filesystem": "root",
            "group": {"name": "root"},
            "path": "",
            "user": {"name": "root"},
            "contents": {
                "source": "data:," + encoded_metadata,
                "verification": {}},
            "mode": 0o640
        }

        for path in path_list:
            storage = payload.setdefault('storage', {})
            try:
                files = storage.setdefault('files', [])
            except AttributeError:
                raise ValueError('Ignition "storage" section must be a map')
            else:
                try:
                    data = ignition_format_metadata.copy()
                    data["path"] = path
                    files.append(data)
                except AttributeError:
                    raise ValueError('Ignition "files" section must be a list')

        return jsonutils.dumps(payload)

    def build_userdata(self, metadata, userdata=None, instance_user=None,
                       user_data_format='HEAT_CFNTOOLS'):
        """Build multipart data blob for CloudInit and Ignition.

        MIGRATED FROM: nova.py lines 333-457
        ADAPTED FOR: SDK - context access same, no nova-specific APIs
        USED BY: server.py handle_create

        Data blob includes user-supplied metadata, user data, and the required
        Heat in-instance configuration.

        :param metadata: metadata dict to inject into instance
        :param userdata: user data string
        :param instance_user: the user to create on the server
        :param user_data_format: Format of user data ('RAW', 'HEAT_CFNTOOLS', 'SOFTWARE_CONFIG')
        :returns: multipart MIME as a string or raw userdata
        """
        if user_data_format == 'RAW':
            return userdata

        is_cfntools = user_data_format == 'HEAT_CFNTOOLS'
        is_software_config = user_data_format == 'SOFTWARE_CONFIG'

        # Ignition format (CoreOS) handling
        if (is_software_config and
                OpenStackSDKPlugin.is_ignition_format(userdata)):
            return OpenStackSDKPlugin.build_ignition_data(metadata, userdata)

        def make_subpart(content, filename, subtype=None):
            """Create MIME subpart."""
            if subtype is None:
                subtype = os.path.splitext(filename)[0]
            if content is None:
                content = ''
            try:
                content.encode('us-ascii')
                charset = 'us-ascii'
            except UnicodeEncodeError:
                charset = 'utf-8'
            msg = (text.MIMEText(content, _subtype=subtype, _charset=charset)
                   if subtype else text.MIMEText(content, _charset=charset))

            msg.add_header('Content-Disposition', 'attachment',
                           filename=filename)
            return msg

        def read_cloudinit_file(fn):
            """Read CloudInit template file from heat package."""
            return pkgutil.get_data(
                'heat', 'cloudinit/%s' % fn).decode('utf-8')

        # Build custom user configuration
        if instance_user:
            config_custom_user = 'user: %s' % instance_user
            # Compatibility workaround for cloud-init 0.6.3
            boothook_custom_user = r"""useradd -m %s
echo -e '%s\tALL=(ALL)\tNOPASSWD: ALL' >> /etc/sudoers
""" % (instance_user, instance_user)
        else:
            config_custom_user = ''
            boothook_custom_user = ''

        # Load and customize CloudInit templates
        cloudinit_config = string.Template(
            read_cloudinit_file('config')).safe_substitute(
                add_custom_user=config_custom_user)
        cloudinit_boothook = string.Template(
            read_cloudinit_file('boothook.sh')).safe_substitute(
                add_custom_user=boothook_custom_user)

        # Build attachment list
        attachments = [(cloudinit_config, 'cloud-config'),
                       (cloudinit_boothook, 'boothook.sh', 'cloud-boothook'),
                       (read_cloudinit_file('part_handler.py'),
                        'part-handler.py')]

        if is_cfntools:
            attachments.append((userdata, 'cfn-userdata', 'x-cfninitdata'))
        elif is_software_config:
            # Parse userdata as multipart if possible
            userdata_parts = None
            try:
                userdata_parts = email.message_from_string(userdata)
            except Exception:
                pass
            if userdata_parts and userdata_parts.is_multipart():
                for part in userdata_parts.get_payload():
                    attachments.append((part.get_payload(),
                                        part.get_filename(),
                                        part.get_content_subtype()))
            else:
                attachments.append((userdata, ''))

        if is_cfntools:
            attachments.append((read_cloudinit_file('loguserdata.py'),
                               'loguserdata.py', 'x-shellscript'))

        # Add metadata
        if metadata:
            attachments.append((jsonutils.dumps(metadata),
                                'cfn-init-data', 'x-cfninitdata'))

        # Add CFN metadata server URL for cfntools
        if is_cfntools:
            heat_client_plugin = self.context.clients.client_plugin('heat')
            cfn_md_url = heat_client_plugin.get_cfn_metadata_server_url()
            attachments.append((cfn_md_url,
                                'cfn-metadata-server', 'x-cfninitdata'))

            # Create boto config for cfntools
            cfn_url = urlparse.urlparse(cfn_md_url)
            is_secure = cfg.CONF.instance_connection_is_secure
            vcerts = cfg.CONF.instance_connection_https_validate_certificates
            boto_cfg = "\n".join(["[Boto]",
                                  "debug = 0",
                                  "is_secure = %s" % is_secure,
                                  "https_validate_certificates = %s" % vcerts,
                                  "cfn_region_name = heat",
                                  "cfn_region_endpoint = %s" %
                                  cfn_url.hostname])
            attachments.append((boto_cfg,
                                'cfn-boto-cfg', 'x-cfninitdata'))

        # Build multipart MIME
        subparts = [make_subpart(*args) for args in attachments]
        mime_blob = multipart.MIMEMultipart(_subparts=subparts)

        return mime_blob.as_string()

    def interface_detach(self, server_id, port_id):
        """Detach a network interface from a server.

        MIGRATED FROM: nova.py lines 813-818
        ADAPTED FOR: SDK - uses compute.delete_server_interface
        USED BY: server.py network update operations

        :param server_id: server ID
        :param port_id: port ID to detach
        :returns: True (always, errors are ignored via ignore_not_found)
        """
        with self.ignore_not_found:
            server = self.fetch_server(server_id)
            if server:
                self.client().compute.delete_server_interface(port_id, server)
        return True

    def interface_attach(self, server_id, port_id=None, net_id=None, fip=None,
                         security_groups=None):
        """Attach a network interface to a server.

        MIGRATED FROM: nova.py lines 820-831
        ADAPTED FOR: SDK - uses compute.create_server_interface
        USED BY: server.py network update operations

        :param server_id: server ID
        :param port_id: port ID to attach (optional)
        :param net_id: network ID to create port on (optional)
        :param fip: fixed IP address (optional)
        :param security_groups: security groups to apply (optional)
        :returns: True if attached, False if server not found
        """
        server = self.fetch_server(server_id)
        if server:
            # SDK MIGRATION: create_server_interface expects different params
            attachment = self.client().compute.create_server_interface(
                server, port_id=port_id, net_id=net_id, fixed_ip=fip)
            if not port_id and security_groups:
                # Update port security groups
                props = {'security_groups': security_groups}
                self.client().network.update_port(
                    attachment.port_id, **props)
            return True
        else:
            return False

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(
            cfg.CONF.max_interface_check_attempts),
        wait=tenacity.wait_exponential(multiplier=0.5, max=12.0),
        retry=tenacity.retry_if_result(client_plugin.retry_if_result_is_false))
    def check_interface_detach(self, server_id, port_id):
        """Check if interface detachment has completed.

        MIGRATED FROM: nova.py lines 833-858
        ADAPTED FOR: SDK - uses network.get_port and fetch_server
        USED BY: server.py check_update_complete

        :param server_id: server ID
        :param port_id: port ID
        :returns: True if detached, False if still attached
        """
        with self.ignore_not_found:
            # Check if port is still bound to this server
            port = self.client().network.get_port(port_id)
            if port and 'device_id' in port and port.device_id == server_id:
                return False

            # Also check server addresses to be sure
            mac_address = port.mac_address if port else None
            if mac_address:
                server = self.fetch_server(server_id)
                if server and hasattr(server, 'addresses'):
                    addresses = server.addresses
                    for net_addrs in addresses.values():
                        for addr in net_addrs:
                            if (addr.get('OS-EXT-IPS:type') == 'fixed' and
                                    mac_address == addr.get('OS-EXT-IPS-MAC:mac_addr')):
                                return False
        return True

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(
            cfg.CONF.max_interface_check_attempts),
        wait=tenacity.wait_fixed(0.5),
        retry=tenacity.retry_if_result(client_plugin.retry_if_result_is_false))
    def check_interface_attach(self, server_id, port_id):
        """Check if interface attachment has completed.

        MIGRATED FROM: nova.py lines 860-875
        ADAPTED FOR: SDK - uses compute.server_interfaces
        USED BY: server.py check_update_complete

        :param server_id: server ID
        :param port_id: port ID
        :returns: True if attached, False if not yet attached
        """
        if not port_id:
            return True

        server = self.fetch_server(server_id)
        if server:
            interfaces = list(self.client().compute.server_interfaces(server))
            for iface in interfaces:
                if iface.port_id == port_id:
                    return True
        return False

    def associate_floatingip(self, server_id, floatingip_id):
        """Associate a floating IP with a server.

        MIGRATED FROM: nova.py lines 765-783
        ADAPTED FOR: SDK - uses compute.server_interfaces
        USED BY: floatingip.py NovaFloatingIpAssociation

        :param server_id: server ID
        :param floatingip_id: floating IP ID
        :raises: exception.Error if no interfaces found
        """
        from heat.common import exception as client_exception
        from oslo_utils import netutils

        server = self.fetch_server(server_id)
        if not server:
            raise client_exception.Error(_('Server %s not found') % server_id)

        iface_list = list(self.client().compute.server_interfaces(server))
        if len(iface_list) == 0:
            raise client_exception.Error(_('No interfaces found for server %s') % server_id)
        if len(iface_list) > 1:
            LOG.warning("Multiple interfaces found for server %s, "
                        "using the first one.", server_id)

        port_id = iface_list[0].port_id
        fixed_ips = iface_list[0].fixed_ips
        fixed_address = next(ip['ip_address'] for ip in fixed_ips
                             if netutils.is_valid_ipv4(ip['ip_address']))
        request_body = {
            'floatingip': {
                'port_id': port_id,
                'fixed_ip_address': fixed_address}}

        self.clients.client('neutron').update_floatingip(floatingip_id,
                                                         request_body)

    def dissociate_floatingip(self, floatingip_id):
        """Dissociate a floating IP.

        MIGRATED FROM: nova.py lines 785-791
        NO CHANGES: Uses Neutron client, SDK-independent
        USED BY: floatingip.py NovaFloatingIpAssociation

        :param floatingip_id: floating IP ID
        """
        request_body = {
            'floatingip': {
                'port_id': None,
                'fixed_ip_address': None}}
        self.clients.client('neutron').update_floatingip(floatingip_id,
                                                         request_body)

    def associate_floatingip_address(self, server_id, fip_address):
        """Associate a floating IP by address.

        MIGRATED FROM: nova.py lines 793-801
        NO CHANGES: Uses Neutron client and associate_floatingip
        USED BY: Legacy compatibility

        :param server_id: server ID
        :param fip_address: floating IP address (not ID)
        """
        from heat.common import exception as client_exception

        fips = self.clients.client(
            'neutron').list_floatingips(
                floating_ip_address=fip_address)['floatingips']
        if len(fips) == 0:
            args = {'ip_address': fip_address}
            raise client_exception.EntityMatchNotFound(entity='floatingip',
                                                       args=args)
        self.associate_floatingip(server_id, fips[0]['id'])

    def dissociate_floatingip_address(self, fip_address):
        """Dissociate a floating IP by address.

        MIGRATED FROM: nova.py lines 803-811
        NO CHANGES: Uses Neutron client and dissociate_floatingip
        USED BY: Legacy compatibility

        :param fip_address: floating IP address (not ID)
        """
        from heat.common import exception as client_exception

        fips = self.clients.client(
            'neutron').list_floatingips(
                floating_ip_address=fip_address)['floatingips']
        if len(fips) == 0:
            args = {'ip_address': fip_address}
            raise client_exception.EntityMatchNotFound(entity='floatingip',
                                                       args=args)
        self.dissociate_floatingip(fips[0]['id'])

    def get_console_urls(self, server):
        """Return dict-like structure of server's console URLs.

        MIGRATED FROM: nova.py lines 670-709
        ADAPTED FOR: SDK - uses compute.get_server_console_url
        USED BY: server.py _resolve_attribute

        The actual console URL is lazily resolved on access.

        :param server: server object
        :returns: dict-like ConsoleUrls object
        """
        client = self.client

        class ConsoleUrls(collections.abc.Mapping):
            def __init__(self, server):
                self.server = server
                self.support_console_types = ['novnc', 'xvpvnc',
                                              'spice-html5', 'rdp-html5',
                                              'serial', 'webmks']

            def __getitem__(self, key):
                try:
                    if key not in self.support_console_types:
                        # SDK doesn't have UnsupportedConsoleType, use generic
                        return _('Unsupported console type: %s') % key

                    # SDK MIGRATION: Use compute.get_server_console_url
                    data = client().compute.get_server_console_url(
                        self.server, console_type=key)

                    # Console data structure from SDK
                    console_data = data.get('remote_console', data.get('console'))
                    url = console_data['url']
                except Exception as e:
                    url = _('Cannot get console url: %s') % str(e)

                return url

            def __len__(self):
                return len(self.support_console_types)

            def __iter__(self):
                return (key for key in self.support_console_types)

        return ConsoleUrls(server)

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


# ============= Compute Constraints =============
# MIGRATED FROM: nova.py lines 858-891


class ComputeBaseConstraint(constraints.BaseCustomConstraint):
    """Base constraint for compute resources.

    MIGRATED FROM: nova.py NovaBaseConstraint (line 858-860)
    ADAPTED FOR: SDK - uses CLIENT_NAME = 'openstack' instead of 'nova'
    """
    resource_client_name = CLIENT_NAME


class ServerConstraint(ComputeBaseConstraint):
    """Constraint for server resources.

    MIGRATED FROM: nova.py lines 863-865
    UNCHANGED
    USED BY: server_interface.py, server.py
    """
    resource_getter_name = 'get_server'


class FlavorConstraint(ComputeBaseConstraint):
    """Constraint for flavor resources.

    MIGRATED FROM: nova.py lines 880-884
    ADAPTED FOR: SDK - expected_exceptions uses ResourceNotFound
    USED BY: flavor.py, server.py
    """
    expected_exceptions = (exceptions.ResourceNotFound,)
    resource_getter_name = 'find_flavor_by_name_or_id'


class KeypairConstraint(ComputeBaseConstraint):
    """Constraint for keypair resources.

    MIGRATED FROM: nova.py lines 868-877
    UNCHANGED
    USED BY: keypair.py, server.py
    """
    resource_getter_name = 'get_keypair'

    def validate_with_client(self, client, key_name):
        if not key_name:
            # Don't validate empty key, which can happen when you
            # use a KeyPair resource
            return True
        super(KeypairConstraint, self).validate_with_client(client, key_name)


class HostConstraint(ComputeBaseConstraint):
    """Constraint for hypervisor host resources.

    MIGRATED FROM: nova.py lines 887-891
    ADAPTED FOR: SDK - expected_exceptions uses ResourceNotFound
    USED BY: host_aggregate.py
    """
    expected_exceptions = (exceptions.ResourceNotFound,)
    resource_getter_name = 'get_host'


class SegmentConstraint(constraints.BaseCustomConstraint):

    expected_exceptions = (exceptions.ResourceNotFound,
                           exceptions.DuplicateResource)

    def validate_with_client(self, client, value):
        sdk_plugin = client.client_plugin(CLIENT_NAME)
        sdk_plugin.find_network_segment(value)
