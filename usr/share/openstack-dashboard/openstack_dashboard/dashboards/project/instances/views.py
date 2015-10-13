# Copyright 2012 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
# Copyright 2012 Nebula, Inc.
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

"""
Views for managing instances.
"""
from django.core.urlresolvers import reverse
from django.core.urlresolvers import reverse_lazy
from django import http
from django import shortcuts
from django.utils.datastructures import SortedDict
from django.utils.translation import ugettext_lazy as _

from horizon import exceptions
from horizon import forms
from horizon import tables
from horizon import tabs
from horizon.utils import memoized
from horizon import workflows

from openstack_dashboard import api

from openstack_dashboard.dashboards.project.instances \
    import console as project_console
from openstack_dashboard.dashboards.project.instances \
    import forms as project_forms
from openstack_dashboard.dashboards.project.instances \
    import tables as project_tables
from openstack_dashboard.dashboards.project.instances \
    import tabs as project_tabs
from openstack_dashboard.dashboards.project.instances \
    import workflows as project_workflows


class IndexView(tables.DataTableView):
    table_class = project_tables.InstancesTable
    template_name = 'project/instances/index.html'

    def has_more_data(self, table):
        return self._more

    def get_data(self):
        marker = self.request.GET.get(
            project_tables.InstancesTable._meta.pagination_param, None)
        search_opts = self.get_filters({'marker': marker, 'paginate': True})
        import logging
        logger = logging.getLogger(__name__)
        #logger.info('khayam')
        import ConfigParser
        config = ConfigParser.ConfigParser()
        config.read('/etc/nova/fireant.conf')
        local = config.get('nova','local')
        tenant=config.get(local, 'ip') # returns 12.2
        from novaclient import client
        total_clusters = config.get('nova','clusters')
        connections = list()
        local = config.get('nova','local')
        for i in range (1,int (total_clusters)+1):
            cluster_name = 'cluster'+str(i)
            if cluster_name != local :
               connection = client.Client(2,config.get(cluster_name, 'tenant') ,config.get(cluster_name, 'user'),config.get(cluster_name, 'pass'),config.get(cluster_name, 'keystone'))
               connections.append(connection)
        servers = list()
        for connection in connections:
            server = connection.servers.list(detailed=True, search_opts=None, marker=None, limit=None)
            servers.append(server)
        allvms = list()
        import ConfigParser
        config = ConfigParser.ConfigParser()
        config.read('/etc/nova/fireant.conf')
        import MySQLdb
        local = config.get('nova','local')
        dip=config.get(local, 'ip') 
        dbase=config.get('sql', 'db') 
        duser=config.get('sql', 'user')
        dpass=config.get('sql', 'pass')
        db = MySQLdb.connect(host=dip, # your host, usually localhost
                     user=duser, # your username
                     passwd=dpass, # your password
                     db=dbase) # name of the data base
        cur = db.cursor()

        for server in servers:
            for vm in server:
                cur.execute ("select * from  vms where uuid = " + "\'" + vm.id +"\'")
                if cur.rowcount > 0 :
                   iid = vm.id
                   iname = vm.name
                   allvms.append(vm)
          
        # Gather our instances
        try:
            instances, self._more = api.nova.server_list(
                self.request,
                search_opts=search_opts)
            import ConfigParser
            config = ConfigParser.ConfigParser()
            config.read('/etc/nova/fireant.conf')

            import MySQLdb
            local = config.get('nova','local')
            dip=config.get(local, 'ip') # returns 12.2
            dbase=config.get('sql', 'db') # returns 12.2
            duser=config.get('sql', 'user') # returns 12.2
            dpass=config.get('sql', 'pass') # returns 12.2
            db = MySQLdb.connect(host=dip, # your host, usually localhost
                     user=duser, # your username
                     passwd=dpass, # your password
                     db=dbase) # name of the data base
            cur = db.cursor()

            instances = instances + allvms #vmlists
        except Exception:
            self._more = False
            instances = []
            exceptions.handle(self.request,
                              _('Unable to retrieve instances.'))

        if instances:
            try:
                api.network.servers_update_addresses(self.request, instances)
            except Exception:
                exceptions.handle(
                    self.request,
                    message=_('Unable to retrieve IP addresses from Neutron.'),
                    ignore=True)

            # Gather our flavors and images and correlate our instances to them
            try:
                flavors = api.nova.flavor_list(self.request)
            except Exception:
                flavors = []
                exceptions.handle(self.request, ignore=True)

            try:
                # TODO(gabriel): Handle pagination.
                images, more, prev = api.glance.image_list_detailed(
                    self.request)

            except Exception:
                images = []
                exceptions.handle(self.request, ignore=True)

            full_flavors = SortedDict([(str(flavor.id), flavor)
                                       for flavor in flavors])
            image_map = SortedDict([(str(image.id), image)
                                    for image in images])

            # Loop through instances to get flavor info.
            for instance in instances:
                if hasattr(instance, 'image'):
                    # Instance from image returns dict
                    if isinstance(instance.image, dict):
                        if instance.image.get('id') in image_map:
                            instance.image = image_map[instance.image['id']]

                try:
                    flavor_id = instance.flavor["id"]
                    if flavor_id in full_flavors:
                        instance.full_flavor = full_flavors[flavor_id]
                    else:
                        # If the flavor_id is not in full_flavors list,
                        # get it via nova api.
                        instance.full_flavor = api.nova.flavor_get(
                            self.request, flavor_id)
                except Exception:
                    msg = _('Unable to retrieve instance size information.')
                    exceptions.handle(self.request, msg)
        return instances

    def get_filters(self, filters):
        filter_field = self.table.get_filter_field()
        filter_action = self.table._meta._filter_action
        if filter_action.is_api_filter(filter_field):
            filter_string = self.table.get_filter_string()
            if filter_field and filter_string:
                filters[filter_field] = filter_string
        return None


class LaunchInstanceView(workflows.WorkflowView):
    workflow_class = project_workflows.LaunchInstance

    def get_initial(self):
        initial = super(LaunchInstanceView, self).get_initial()
        initial['project_id'] = self.request.user.tenant_id
        initial['user_id'] = self.request.user.id
        return initial

    def get_context_data(self, **kwargs):
        context = super(LaunchInstanceView, self).get_context_data(**kwargs)
        # Data from URL are always in self.kwargs, here we pass the data
        # to the template.
        #context["possible"] = kwargs['possible']
        # Data contributed by Workflow's Steps are in the
        # context['workflow'].context list. We can use that in the
        # template too.
        return context

def console(request, instance_id):
    try:
        # TODO(jakedahn): clean this up once the api supports tailing.
        tail = request.GET.get('length', None)
        data = api.nova.server_console_output(request,
                                              instance_id,
                                              tail_length=tail)
    except Exception:
        data = _('Unable to get log for instance "%s".') % instance_id
        exceptions.handle(request, ignore=True)
    response = http.HttpResponse(content_type='text/plain')
    response.write(data)
    response.flush()
    return response


def vnc(request, instance_id):
    try:
        instance = api.nova.server_get(request, instance_id)
        console_url = project_console.get_console(request, 'VNC', instance)
        return shortcuts.redirect(console_url)
    except Exception:
        redirect = reverse("horizon:project:instances:index")
        msg = _('Unable to get VNC console for instance "%s".') % instance_id
        exceptions.handle(request, msg, redirect=redirect)


def spice(request, instance_id):
    try:
        instance = api.nova.server_get(request, instance_id)
        console_url = project_console.get_console(request, 'SPICE', instance)
        return shortcuts.redirect(console_url)
    except Exception:
        redirect = reverse("horizon:project:instances:index")
        msg = _('Unable to get SPICE console for instance "%s".') % instance_id
        exceptions.handle(request, msg, redirect=redirect)


def rdp(request, instance_id):
    try:
        instance = api.nova.server_get(request, instance_id)
        console_url = project_console.get_console(request, 'RDP', instance)
        return shortcuts.redirect(console_url)
    except Exception:
        redirect = reverse("horizon:project:instances:index")
        msg = _('Unable to get RDP console for instance "%s".') % instance_id
        exceptions.handle(request, msg, redirect=redirect)


class UpdateView(workflows.WorkflowView):
    workflow_class = project_workflows.UpdateInstance
    success_url = reverse_lazy("horizon:project:instances:index")

    def get_context_data(self, **kwargs):
        context = super(UpdateView, self).get_context_data(**kwargs)
        context["instance_id"] = self.kwargs['instance_id']
        return context

    @memoized.memoized_method
    def get_object(self, *args, **kwargs):
        instance_id = self.kwargs['instance_id']
        try:
            return api.nova.server_get(self.request, instance_id)
        except Exception:
            redirect = reverse("horizon:project:instances:index")
            msg = _('Unable to retrieve instance details.')
            exceptions.handle(self.request, msg, redirect=redirect)

    def get_initial(self):
        initial = super(UpdateView, self).get_initial()
        initial.update({'instance_id': self.kwargs['instance_id'],
                'name': getattr(self.get_object(), 'name', '')})
        return initial


class RebuildView(forms.ModalFormView):
    form_class = project_forms.RebuildInstanceForm
    template_name = 'project/instances/rebuild.html'
    success_url = reverse_lazy('horizon:project:instances:index')

    def get_context_data(self, **kwargs):
        context = super(RebuildView, self).get_context_data(**kwargs)
        context['instance_id'] = self.kwargs['instance_id']
        context['can_set_server_password'] = api.nova.can_set_server_password()
        return context

    def get_initial(self):
        return {'instance_id': self.kwargs['instance_id']}


class DecryptPasswordView(forms.ModalFormView):
    form_class = project_forms.DecryptPasswordInstanceForm
    template_name = 'project/instances/decryptpassword.html'
    success_url = reverse_lazy('horizon:project:instances:index')

    def get_context_data(self, **kwargs):
        context = super(DecryptPasswordView, self).get_context_data(**kwargs)
        context['instance_id'] = self.kwargs['instance_id']
        context['keypair_name'] = self.kwargs['keypair_name']
        return context

    def get_initial(self):
        return {'instance_id': self.kwargs['instance_id'],
                'keypair_name': self.kwargs['keypair_name']}


class DetailView(tabs.TabView):
    tab_group_class = project_tabs.InstanceDetailTabs
    template_name = 'project/instances/detail.html'
    redirect_url = 'horizon:project:instances:index'

    def get_context_data(self, **kwargs):
        context = super(DetailView, self).get_context_data(**kwargs)
        instance = self.get_data()
        context["instance"] = instance
        table = project_tables.InstancesTable(self.request)
        context["url"] = reverse(self.redirect_url)
        context["actions"] = table.render_row_actions(instance)
        return context

    @memoized.memoized_method
    def get_data(self):
        try:
            instance_id = self.kwargs['instance_id']
            instance = api.nova.server_get(self.request, instance_id)
            status_label = [label for (value, label) in
                            project_tables.STATUS_DISPLAY_CHOICES
                            if value.lower() ==
                            (instance.status or '').lower()]
            if status_label:
                instance.status_label = status_label[0]
            else:
                instance.status_label = instance.status
            instance.volumes = api.nova.instance_volumes_list(self.request,
                                                              instance_id)
            # Sort by device name
            instance.volumes.sort(key=lambda vol: vol.device)
            instance.full_flavor = api.nova.flavor_get(
                self.request, instance.flavor["id"])
            instance.security_groups = api.network.server_security_groups(
                self.request, instance_id)
        except Exception:
            redirect = reverse(self.redirect_url)
            exceptions.handle(self.request,
                              _('Unable to retrieve details for '
                                'instance "%s".') % instance_id,
                                redirect=redirect)
            # Not all exception types handled above will result in a redirect.
            # Need to raise here just in case.
            raise exceptions.Http302(redirect)
        try:
            api.network.servers_update_addresses(self.request, [instance])
        except Exception:
            exceptions.handle(
                self.request,
                _('Unable to retrieve IP addresses from Neutron for instance '
                  '"%s".') % instance_id, ignore=True)
        return instance

    def get_tabs(self, request, *args, **kwargs):
        instance = self.get_data()
        return self.tab_group_class(request, instance=instance, **kwargs)


class ResizeView(workflows.WorkflowView):
    workflow_class = project_workflows.ResizeInstance
    success_url = reverse_lazy("horizon:project:instances:index")

    def get_context_data(self, **kwargs):
        context = super(ResizeView, self).get_context_data(**kwargs)
        context["instance_id"] = self.kwargs['instance_id']
        return context

    @memoized.memoized_method
    def get_object(self, *args, **kwargs):
        instance_id = self.kwargs['instance_id']
        try:
            instance = api.nova.server_get(self.request, instance_id)
            flavor_id = instance.flavor['id']
            flavors = self.get_flavors()
            if flavor_id in flavors:
                instance.flavor_name = flavors[flavor_id].name
            else:
                flavor = api.nova.flavor_get(self.request, flavor_id)
                instance.flavor_name = flavor.name
        except Exception:
            redirect = reverse("horizon:project:instances:index")
            msg = _('Unable to retrieve instance details.')
            exceptions.handle(self.request, msg, redirect=redirect)
        return instance

    @memoized.memoized_method
    def get_flavors(self, *args, **kwargs):
        try:
            flavors = api.nova.flavor_list(self.request)
            return SortedDict((str(flavor.id), flavor) for flavor in flavors)
        except Exception:
            redirect = reverse("horizon:project:instances:index")
            exceptions.handle(self.request,
                _('Unable to retrieve flavors.'), redirect=redirect)

    def get_initial(self):
        initial = super(ResizeView, self).get_initial()
        _object = self.get_object()
        if _object:
            initial.update({'instance_id': self.kwargs['instance_id'],
                'name': getattr(_object, 'name', None),
                'old_flavor_id': _object.flavor['id'],
                'old_flavor_name': getattr(_object, 'flavor_name', ''),
                'flavors': self.get_flavors()})
        return initial
