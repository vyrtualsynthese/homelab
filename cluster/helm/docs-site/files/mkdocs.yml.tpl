site_name: "{{site_name}}"
docs_dir: "{{docs_dir}}"
site_dir: "{{site_dir}}"
use_directory_urls: true
theme:
  name: material
  # Absolute paths: a site is served under /<repo>/<ref>/, so the mark and the
  # stylesheet live once at /brand/ instead of once per site. The builder
  # publishes that directory before it builds anything.
  logo: /brand/ashulab-badge-transparent.svg
  favicon: /brand/ashulab-badge.svg
  # No Google Fonts request: the site answers on the intranet and behind a gate,
  # and the system stack reads the same.
  font: false
  features: [navigation.sections, navigation.top, content.code.copy]
  palette:
    - scheme: slate
      primary: black # the named colour closest to the brand; /brand/ashulab.css sets #0B1220
      accent: cyan # likewise, for #2DD4FF
extra_css:
  - /brand/ashulab.css
markdown_extensions:
  - admonition
  - attr_list
  - tables
  - toc: {permalink: true}
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
          format: !!python/name:pymdownx.superfences.fence_code_format
plugins: [search]
