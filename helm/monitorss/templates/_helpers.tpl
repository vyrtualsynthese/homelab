# Bot template

{{/*
Expand the name of the chart.
*/}}
{{- define "bot.name" -}}
{{- default .Chart.Name .Values.bot.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "bot.fullname" -}}
{{- if .Values.bot.fullnameOverride }}
{{- .Values.bot.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.bot.nameOverride }}
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
{{- define "bot.labels" -}}
helm.sh/chart: {{ include "chart" . }}
{{ include "bot.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "bot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "bot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "bot.serviceAccountName" -}}
{{- if .Values.bot.serviceAccount.create }}
{{- default (include "bot.fullname" .) .Values.bot.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.bot.serviceAccount.name }}
{{- end }}
{{- end }}

# Mongo template

{{/*
Expand the name of the chart.
*/}}
{{- define "mongo.name" -}}
{{- default .Chart.Name .Values.mongo.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "mongo.fullname" -}}
{{- if .Values.mongo.fullnameOverride }}
{{- .Values.mongo.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.mongo.nameOverride }}
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
{{- define "mongo.labels" -}}
helm.sh/chart: {{ include "chart" . }}
{{ include "mongo.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "mongo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mongo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "mongo.serviceAccountName" -}}
{{- if .Values.mongo.serviceAccount.create }}
{{- default (include "mongo.fullname" .) .Values.mongo.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.mongo.serviceAccount.name }}
{{- end }}
{{- end }}
