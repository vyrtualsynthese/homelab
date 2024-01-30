# App template

{{/*
Expand the name of the chart.
*/}}
{{- define "guacamole.name" -}}
{{- default .Chart.Name .Values.guacamole.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "guacamole.fullname" -}}
{{- if .Values.guacamole.fullnameOverride }}
{{- .Values.guacamole.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.guacamole.nameOverride }}
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
{{- define "chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "guacamole.labels" -}}
helm.sh/chart: {{ include "chart" . }}
{{ include "guacamole.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "guacamole.selectorLabels" -}}
app.kubernetes.io/name: {{ include "guacamole.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "guacamole.serviceAccountName" -}}
{{- if .Values.guacamole.serviceAccount.create }}
{{- default (include "guacamole.fullname" .) .Values.guacamole.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.guacamole.serviceAccount.name }}
{{- end }}
{{- end }}

# guacd template

{{/*
Expand the name of the chart.
*/}}
{{- define "guacd.name" -}}
{{- default .Chart.Name .Values.guacd.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "guacd.fullname" -}}
{{- if .Values.guacd.fullnameOverride }}
{{- .Values.guacd.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.guacd.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "guacd.labels" -}}
helm.sh/chart: {{ include "chart" . }}
{{ include "guacd.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "guacd.selectorLabels" -}}
app.kubernetes.io/name: {{ include "guacd.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "guacd.serviceAccountName" -}}
{{- if .Values.db.serviceAccount.create }}
{{- default (include "guacd.fullname" .) .Values.db.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.db.serviceAccount.name }}
{{- end }}
{{- end }}


# DB template

{{/*
Expand the name of the chart.
*/}}
{{- define "db.name" -}}
{{- default .Chart.Name .Values.db.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "db.fullname" -}}
{{- if .Values.db.fullnameOverride }}
{{- .Values.db.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.db.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "db.labels" -}}
helm.sh/chart: {{ include "chart" . }}
{{ include "db.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "db.selectorLabels" -}}
app.kubernetes.io/name: {{ include "db.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "db.serviceAccountName" -}}
{{- if .Values.db.serviceAccount.create }}
{{- default (include "db.fullname" .) .Values.db.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.db.serviceAccount.name }}
{{- end }}
{{- end }}
