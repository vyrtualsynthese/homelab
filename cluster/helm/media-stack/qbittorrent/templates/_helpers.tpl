{{/*
Name helpers.

These are deliberately NOT prefixed with the chart name, matching the reference
chart (`autobrr`) so a copy works without a rename sweep. The trade-off: do not
use two of these charts as subcharts of the same parent, or the helper names
collide.
*/}}

{{/* Chart name, overridable. */}}
{{- define "app.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Fully qualified release name. */}}
{{- define "app.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Chart name and version, as used by the helm.sh/chart label. */}}
{{- define "app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Common labels, applied to every object. */}}
{{- define "app.labels" -}}
helm.sh/chart: {{ include "app.chart" . }}
{{ include "app.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Selector labels. Changing these orphans the existing pods. */}}
{{- define "app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Service account name, honouring serviceAccount.create. */}}
{{- define "app.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "app.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* WireGuard claim name: explicit `storage.vpn.claimName`, else `<fullname>-vpn-config`.
The derived name equals the claim the live pod mounts (`qbittorrent-vpn-config`),
so the default moves no data. */}}
{{- define "app.vpnClaimName" -}}
{{- default (printf "%s-vpn-config" (include "app.fullname" .)) .Values.storage.vpn.claimName }}
{{- end }}

{{/* Container port: explicit container.port, else the service port. */}}
{{- define "app.containerPort" -}}
{{- default .Values.service.port .Values.container.port }}
{{- end }}
