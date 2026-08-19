from django.contrib import admin

from .models import NetworkAddress, ScanRun, Subnet


@admin.register(Subnet)
class SubnetAdmin(admin.ModelAdmin):
    list_display = ('cidr', 'name', 'is_scan_enabled', 'probe_ports')
    list_editable = ('is_scan_enabled', 'probe_ports')


@admin.register(NetworkAddress)
class NetworkAddressAdmin(admin.ModelAdmin):
    list_display = ('ip', 'name', 'hostname', 'kind', 'os_guess', 'last_seen_at', 'is_excluded')
    list_filter = ('subnet', 'kind', 'os_guess', 'is_excluded')
    search_fields = ('ip', 'name', 'hostname', 'mac')
    readonly_fields = NetworkAddress.SCAN_FIELDS


@admin.register(ScanRun)
class ScanRunAdmin(admin.ModelAdmin):
    list_display = ('subnet', 'started_at', 'finished_at', 'scanned', 'responded', 'created')
    readonly_fields = ('subnet', 'started_by', 'started_at', 'finished_at',
                       'scanned', 'responded', 'created', 'error')
