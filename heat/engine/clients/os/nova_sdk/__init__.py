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

"""Nova client plugin using OpenStack SDK.

This module provides a backward-compatible Nova client plugin that uses
OpenStack SDK instead of python-novaclient.

Architecture:
    - NovaSdkClientPlugin: Main plugin class (inherits from OpenStackSDKPlugin)
    - constraints: Nova-specific resource constraints

Design Pattern: Adapter Pattern + Inheritance
    OpenStackSDKPlugin (generic SDK base)
        ↑
        └── NovaSdkClientPlugin (Nova-specific adapter)

Usage:
    Resources use default_client_name = 'nova' to automatically load this plugin.
    The plugin provides all Nova-specific operations while inheriting generic
    SDK functionality from OpenStackSDKPlugin.
"""

from heat.engine.clients.os.nova_sdk.nova import NovaSdkClientPlugin

__all__ = ['NovaSdkClientPlugin']
