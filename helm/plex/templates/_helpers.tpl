# App template

{{/*
Expand the name of the chart.
*/}}
{{- define "nextcloud.plex.name" -}}
{{- default .Chart.Name .Values.plex.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "nextcloud.plex.fullname" -}}
{{- if .Values.plex.fullnameOverride }}
{{- .Values.plex.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.plex.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "nextcloud.plex.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "nextcloud.plex.labels" -}}
helm.sh/chart: {{ include "nextcloud.plex.chart" . }}
{{ include "nextcloud.plex.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "nextcloud.plex.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nextcloud.plex.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "nextcloud.plex.serviceAccountName" -}}
{{- if .Values.plex.serviceAccount.create }}
{{- default (include "nextcloud.plex.fullname" .) .Values.plex.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.plex.serviceAccount.name }}
{{- end }}
{{- end }}
