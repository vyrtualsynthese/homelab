{{/*
Name of the instance. Deliberately fixed and not release-prefixed: every
consumer connects to postgres.databases.svc.cluster.local, so the name must not
move when the release is renamed.
*/}}
{{- define "postgres.name" -}}
postgres
{{- end }}

{{/*
Common labels.
*/}}
{{- define "postgres.labels" -}}
app: {{ include "postgres.name" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- with .Chart.AppVersion }}
app.kubernetes.io/version: {{ . | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
