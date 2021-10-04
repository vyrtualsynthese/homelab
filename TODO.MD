+ Dashy
    + Config :
        + Login and guest accounts
        + Define Themes and design
        + Links :
          + Hue Bridge
          + dashy.local
          + home.local
          + home.local/dashboard/
    + Build config and sources into static nginx image
    + Dashi CI and terraforming
        + Login mode
        + Guest Mode
        + Add Traefik Dashboard Link
        + Build image Dashy
        + Push to dockerhub repo
        + update k3s cluster to use new image
+ Monitoring
    + Grafana
+ n8n.io
  + Implement Solution
+ OpenHab
  + Implement Solution
+ Torrent Web Client
+ Heatzy automation
    + Plugin Heatzy api
    + Plugin Switchbot API
    + Create a function that check temps and pilot heatzy
+ Exporters
    + Philips Hue prometheus exporter
    + Switchbot Prometheus exporter
+ Pi-hole
    + Configure Pi-Hole
    + Configure Pi-Hole DNS local network

      LATER:
+ Setup orange DynDNS
    + home.ashudev.com to k8s cluster
+ Terraform
    + Job to apply kubectl commands via terraform
    + Job to apply Helm via terraform
+ SSL
    + Implement cert manager solution for web and local domain.
+ Helm Setup
    + setup Helm pour k3s in infra main
    + Integrate Helm in Dashy CI job