+ Ansible
  + Upgrade all improvement (read comments)
  + setup node improvement (read comments)
  + Improve backup setup
  + Setup Script for Gaming PC
+ Switch ultrafaststorage to raid 0
  + Put tdarr cache to ultrafaststorage
+ Setup Minio
  + Backup of longhorn
  + Change restic backup to Minio
  + Backup Minio to S3
+ Hardware
  + Configure 10g switch
  + Plex/bittorrent machin
    + NVME as root Storage
    + SATA SSD for longhorn
    + GPU as backup nvenc encode for plex
  + Backup nas
    + Big HDD
    + Minio as docker compose
  + Rackable Nas case
    + compact
    + lot of space for HDD
    + Sata expension card
  + Find solution for r/w heavy applications for temporary files
+ Apps
  + factorio server
  + Doc
    + Implement https://docusaurus.io/ for documentation
    + https://github.com/factoriotools/factorio-docker
      + Write guide for Media stack usage
      + Write guide for book upload and read Usage
      + List of available services
      + Password protected
  + Inventory System
  + https://snipeitapp.com/
  + Photo management system
  + Music Management system
  + Home assistant
    + Implement Solution
  + https://www.curseforge.com/minecraft/mc-mods/plan-player-analytics
+ Admin
  + Steam api pull games list to merged rss feed
  + Ansible
    + Install ZSH to every nodes
    + Automate all operations
  + Fluentd + Loki for log management
+ Hardware
  + KVM
    + PiKVM
+ Setup Prometheus exporter for raspberry pi
  + Pihole Exporter
  + Wireguard Exporter

+ Readme~~~~

+ global databases ?

+ CI
  + github actions ci node
  + Terraform to manage PV, namespaces, storage class
+ Terraform
    + Job to apply kubectl commands via terraform
    + Job to apply Helm via terraform
