{{/*
The chart deploys through the bjw-s `common` library, which already owns the
fullname rule (release name, or release-chart when they differ) and the labels.
Wrapping it here keeps the IngressRoute name identical to the Service name the
library renders, without copying the media-stack `app.*` helper contract.
*/}}
{{- define "vikunja.fullname" -}}
{{- include "bjw-s.common.lib.chart.names.fullname" . -}}
{{- end -}}
