+ Refacto
  + nextcloud app & nextcloud web config injection via volume and initContainer
  + Switch to Dashy node runtime and initContainer for conf via volume
  + Inject home via initContainer github pull then volume
  + Overviewer conf via initContainer
  + Overviewer cron via helm
+ Gracefull shutdown kub
  + for minecraft server
  + for databases
+ health check for all pods
  + next cloud php health check
  + minecraft health check
+ Securisation cluster
  + Change all DB password
  + Change all apps password
  + Private Network HTTPS
  + Refacto Helm chart
  + Manage https
+ Connect Cluster to internet
  + dashy.home.ashudev.com
  + nextcloud.home.ashudev.com
  + radarr.home.ashudev.com
  + sonarr.home.ashudev.com
  + qbittorrent.home.ashudev.com
  + plex.home.ashudev.com
  + jacket.home.ashudev.com
+ Doc
  + Implement documentation solution
+ Loki for log management
+ Steam trade bot https://github.com/Refloow/Steam-Card-Bot-PRO/wiki/Installation
+ Global smtp server
+ Photo management system
+ Book Management system
+ Music Management system
+ Ansible
  + Update backup script
  + Install ZSH to every nodes
  + Write Ansible runner setup script
+ Wireguard VPN ???
+ global databases
+ Egress VPN Gateway
+ Docker Repo
  + Docker repo on NaS
+ Infra
  + Power Supply
  + Better Switch
+ Home assistant
  + Implement Solution
+ CI
  + github actions ci node
+ KVM
  + PiKVM
+ Terraform
    + Job to apply kubectl commands via terraform
    + Job to apply Helm via terraform
