{{/*
Name helpers.

These are deliberately NOT prefixed with the chart name, so a chart can be
rebuilt from the reference without a rename sweep. The trade-off: do not use two
of these charts as subcharts of the same parent, or the helper names collide.
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

{{/*
Starr instance environment.

Takes a dict with `prefix` (SONARR, RADARR, ...), `index` (the number in the
variable name) and `instance` (the values block). Every key is rendered; the API
key never appears as a value, only as a secretKeyRef.
*/}}
{{- define "app.starrEnv" -}}
{{- $prefix := .prefix -}}
{{- $index := .index -}}
{{- $instance := .instance -}}
- name: UN_{{ $prefix }}_{{ $index }}_URL
  value: {{ $instance.url | quote }}
- name: UN_{{ $prefix }}_{{ $index }}_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $instance.apiKeySecret.name }}
      key: {{ $instance.apiKeySecret.key | default "secret" }}
{{- range $n, $path := $instance.paths }}
- name: UN_{{ $prefix }}_{{ $index }}_PATHS_{{ $n }}
  value: {{ $path | quote }}
{{- end }}
- name: UN_{{ $prefix }}_{{ $index }}_PROTOCOLS
  value: {{ $instance.protocols | quote }}
- name: UN_{{ $prefix }}_{{ $index }}_TIMEOUT
  value: {{ $instance.timeout | quote }}
- name: UN_{{ $prefix }}_{{ $index }}_DELETE_ORIG
  value: {{ $instance.deleteOrig | toString | quote }}
- name: UN_{{ $prefix }}_{{ $index }}_DELETE_DELAY
  value: {{ $instance.deleteDelay | quote }}
{{- end }}
