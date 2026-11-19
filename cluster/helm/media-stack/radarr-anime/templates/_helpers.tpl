{{/*
Name helpers.

These are deliberately NOT prefixed with the chart name, following the reference
chart this one was written from: a chart copied from this one works without renaming
anything in here. The trade-off is the usual one — do not use two of these charts as
subcharts of the same parent.
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

{{/* Configuration volume claim: an existing claim, or one this chart creates. */}}
{{- define "app.configClaimName" -}}
{{- default (printf "%s-config" (include "app.fullname" .)) .Values.storage.existingClaim }}
{{- end }}

{{/* Container port: explicit container.port, else the service port. */}}
{{- define "app.containerPort" -}}
{{- default .Values.service.port .Values.container.port }}
{{- end }}

{{/* API-key Secret name for the exporter: its own value, else the fullname. */}}
{{- define "app.exportarrSecretName" -}}
{{- default (include "app.fullname" .) .Values.exportarr.apiKey.secretName }}
{{- end }}
