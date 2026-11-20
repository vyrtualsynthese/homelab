{{/*
The S3 URL of one restic repository.
Call: include "backup.repository" (dict "root" $ "name" "nodes-nas")
*/}}
{{- define "backup.repository" -}}
{{- $r := .root.Values.repository -}}
s3:https://{{ $r.endpoint }}/{{ $r.bucket }}/{{ .name }}
{{- end -}}

{{/*
One image from values.image, as <repository>:<tag>.
Call: include "backup.image" (dict "root" $ "name" "restic")
*/}}
{{- define "backup.image" -}}
{{- $img := index .root.Values.image .name -}}
{{ $img.repository }}:{{ $img.tag }}
{{- end -}}

{{/*
The standard labels of a backup object.
Call: include "backup.labels" (dict "root" $ "stream" "nodes-nas")
*/}}
{{- define "backup.labels" -}}
app.kubernetes.io/name: backup
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
homelab.io/backup-stream: {{ .stream }}
{{- end -}}
