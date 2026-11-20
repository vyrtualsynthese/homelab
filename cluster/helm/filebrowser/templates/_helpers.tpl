{{/*
Name helpers.

These are deliberately NOT prefixed with the chart name, matching the reference
chart in media-stack/autobrr. Do not use this chart as a subchart alongside
another chart that carries the same helper names.
*/}}

{{/* Chart name, overridable. */}}
{{- define "app.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified release name.

This is NOT the reference chart's release-plus-chart-name shape. The workload
this chart owns was rendered by the community filebrowser-quantum chart under
the release name, so the live objects are named
`<release>-filebrowser-quantum`. Keeping that string means ArgoCD patches the
existing Deployment (whose selector is immutable) and the existing claims
instead of replacing them or orphaning database.db.
*/}}
{{- define "app.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-filebrowser-quantum" .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/* Chart name and version, as used by the helm.sh/chart label. */}}
{{- define "app.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels.

These are the two labels the previous chart rendered and the Deployment's
selector is immutable, so they cannot be replaced by the standard
app.kubernetes.io pair without deleting and recreating the Deployment. The
standard labels are added alongside them below.
*/}}
{{- define "app.selectorLabels" -}}
app: filebrowser-quantum
release: {{ .Release.Name }}
{{- end }}

{{/* Common labels, applied to every object. */}}
{{- define "app.labels" -}}
helm.sh/chart: {{ include "app.chart" . }}
{{ include "app.selectorLabels" . }}
app.kubernetes.io/name: {{ include "app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Service account name, honouring serviceAccount.create. */}}
{{- define "app.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "app.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Claim names. They must stay identical to the previous chart's. */}}
{{- define "app.configClaimName" -}}
{{- printf "%s-config" (include "app.fullname" .) }}
{{- end }}

{{- define "app.dataClaimName" -}}
{{- printf "%s-data" (include "app.fullname" .) }}
{{- end }}
