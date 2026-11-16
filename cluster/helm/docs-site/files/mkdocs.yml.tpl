site_name: "{{site_name}}"
docs_dir: "{{docs_dir}}"
site_dir: "{{site_dir}}"
use_directory_urls: true
theme:
  name: material
  features: [navigation.sections, navigation.top, content.code.copy]
  palette:
    - scheme: slate
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
