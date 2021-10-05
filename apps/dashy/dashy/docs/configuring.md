# Configuring

All app configuration is specified in [`/public/conf.yml`](https://github.com/Lissy93/dashy/blob/master/public/conf.yml) which is in [YAML Format](https://yaml.org/) format.

---

#### Contents

- [**`pageInfo`**](#pageinfo) - Header text, footer, title, navigation, etc
	- [`navLinks`](#pageinfonavlinks-optional) - Navigation bar items and links
- [**`appConfig`**](#appconfig-optional) - Main application settings
	- [`webSearch`](#appconfigwebsearch-optional) - Configure web search engine options
	- [`hideComponents`](#appconfighidecomponents-optional) - Show/ hide page components
	- [`auth`](#appconfigauth-optional) - Built-in authentication setup
	  - [`users`](#appconfigauthusers-optional) - Setup for simple auth
	  - [`keycloak`](#appconfigauthkeycloak-optional) - Auth using Keycloak
- [**`sections`**](#section) - List of sections
	- [`displayData`](#sectiondisplaydata-optional) - Section display settings
	- [`icon`](#sectionicon-and-sectionitemicon) - Icon for a section
	- [`items`](#sectionitem) - List of items
		- [`icon`](#sectionicon-and-sectionitemicon) - Icon for an item
- [**Notes**](#notes)
	- [About YAML](#about-yaml)
	- [Config Saving Methods](#config-saving-methods)
	- [Preventing Changes](#preventing-changes-being-written-to-disk)
	- [Example](#example)

---

Tips:
- You may find it helpful to look at some sample config files to get you started, a collection of which can be found [here](https://gist.github.com/Lissy93/000f712a5ce98f212817d20bc16bab10)
- You can check that your config file fits the schema, by running `yarn validate-config`
- After modifying your config, the app needs to be recompiled, by running `yarn build`  - this happens automatically whilst the app is running if you're using Docker
- It is recommended to make and keep a backup of your config file. You can download your current config through the UI either from the Config menu, or using the `/download` endpoint. Alternatively, you can use the [Cloud Backup](./docs/backup-restore.md) feature.
- The config can also be modified directly through the UI, validated and written to the conf.yml file.
- All fields are optional, unless otherwise stated.

---

### Top-Level Fields

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`pageInfo`** | `object` | Required | Basic meta data like title, description, nav bar links, footer text. See [`pageInfo`](#pageinfo)
**`appConfig`** | `object` | _Optional_ | Settings related to how the app functions, including API keys and global styles. See [`appConfig`](#appconfig-optional)
**`sections`** | `array` | Required | An array of sections, each containing an array of items, which will be displayed as links. See [`section`](#section)

**[⬆️ Back to Top](#configuring)**

### `PageInfo`

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`title`** | `string` |  Required | Your dashboard title, displayed in the header and browser tab
**`description`** | `string` | _Optional_ | Description of your dashboard, also displayed as a subtitle
**`navLinks`** | `array` | _Optional_ | Optional list of a maximum of 6 links, which will be displayed in the navigation bar. See [`navLinks`](#pageinfonavlinks-optional)
**`footerText`** | `string` | _Optional_ | Text to display in the footer (note that this will override the default footer content). This can also include HTML and inline CSS
**`logo`** | `string` | _Optional_ | The path to an image to display in the header (to the right of the title). This can be either local, where `/` is the root of `./public`, or any remote image, such as `https://i.ibb.co/yhbt6CY/dashy.png`. It's recommended to scale your image down, so that it doesn't impact load times

**[⬆️ Back to Top](#configuring)**

### `pageInfo.navLinks` _(optional)_

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`title`** | `string` |  Required | The text to display on the link button
**`path`** | `string` | Required | The URL to navigate to when clicked. Can be relative (e.g. `/about`) or absolute (e.g. `https://example.com` or `http://192.168.1.1`)

**[⬆️ Back to Top](#configuring)**

### `appConfig` _(optional)_

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`language`** | `string` | _Optional_ | The 2 (or 4-digit) [ISO 639-1 code](https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes) for your language, e.g. `en` or `en-GB`. This must be a language that the app has already been [translated](https://github.com/Lissy93/dashy/tree/master/src/assets/locales) into. If your language is unavailable, Dashy will fallback to English. By default Dashy will attempt to auto-detect your language, although this may not work on some privacy browsers.
**`startingView`** | `enum` | _Optional_ | Which page to load by default, and on the base page or domain root. You can still switch to different views from within the UI. Can be either `default`, `minimal` or `workspace`. Defaults to `default`
**`statusCheck`** | `boolean` | _Optional_ | When set to `true`, Dashy will ping each of your services and display their status as a dot next to each item. This can be overridden by setting `statusCheck` under each item. Defaults to `false`
**`statusCheckInterval`** | `boolean` | _Optional_ | The number of seconds between checks. If set to `0` then service will only be checked on initial page load, which is usually the desired functionality. If value is less than `10` you may experience a hit in performance. Defaults to `0`
**`webSearch`** | `object` | _Optional_ | Configuration options for the web search feature, set your default search engine, opening method or disable web search. See [`webSearch`](#appconfigwebsearch-optional)
**`backgroundImg`** | `string` | _Optional_ | Path to an optional full-screen app background image. This can be either remote (http) or local (/). Note that this will slow down initial load
**`enableFontAwesome`** | `boolean` | _Optional_ | Where `true` is enabled, if left blank font-awesome will be enabled only if required by 1 or more icons
**`fontAwesomeKey`** | `string` | _Optional_ | If you have a font-awesome key, then you can use it here and make use of premium icons. It is a 10-digit alpha-numeric string from you're FA kit URL  (e.g. `13014ae648`)
**`faviconApi`** | `enum` | _Optional_ | Only applicable if you are using favicons for item icons. Specifies which service to use to resolve favicons. Set to `local` to do this locally, without using an API. Services running locally will use this option always. Available options are: `local`, `faviconkit`, `google`, `clearbit`, `webmasterapi` and `allesedv`. Defaults to `faviconkit`. See [Icons](/docs/icons.md#favicons) for more info
**`auth`** | `object` | _Optional_ | All settings relating to user authentication. See [`auth`](#appconfigauth-optional)
**`layout`** | `enum` | _Optional_ | Layout for homepage, either `horizontal`, `vertical` or `auto`. Defaults to `auto`. This specifies the layout and direction of how sections are positioned on the home screen. This can also be modified and overridden from the UI.
**`iconSize`** | `enum` | _Optional_ | The size of link items / icons. Can be either `small`, `medium,` or `large`. Defaults to `medium`. This can also be set directly from the UI.
**`colCount`** | `number` | _Optional_ | The number of columns of sections displayed on the homepage, using the default view. Should be in integer between `1` and `8`. Note that by default this is applied responsively, based on current screen size, and specifying a value here will override this behavior, which may not be desirable.
**`theme`** | `string` | _Optional_ | The default theme for first load (you can change this later from the UI)
**`cssThemes`** | `string[]` | _Optional_ | An array of custom theme names which can be used in the theme switcher dropdown
**`customColors`** | `object`| _Optional_ | Enables you to apply a custom color palette to any given theme. Use the theme name (lowercase) as the key, for an object including key-value-pairs, with the color variable name as keys, and 6-digit hex code as value. See [Theming](/docs/theming.md#modifying-theme-colors) for more info
**`externalStyleSheet`** | `string`  or `string[]` | _Optional_ | Either a URL to an external stylesheet or an array or URLs, which can be applied as themes within the UI
**`customCss`** | `string` | _Optional_ | Raw CSS that will be applied to the page. This can also be set from the UI. Please minify it first.
**`hideComponents`** | `object` | _Optional_ | A list of key page components (header, footer, search, settings, etc) that are present by default, but can be removed using this option. See [`appConfig.hideComponents`](#appconfighideComponents-optional)
**`enableMultiTasking`** | `boolean` | _Optional_ | If set to true, will keep apps open in the background when in the workspace view. Useful for quickly switching between multiple sites, and preserving their state, but comes at the cost of performance.
**`workspaceLandingUrl`** | `string` | _Optional_ | The URL or an app, service or website to launch when the workspace view is opened, before another service has been launched
**`allowConfigEdit`** | `boolean` | _Optional_ | Should prevent / allow the user to write configuration changes to the conf.yml from the UI. When set to `false`, the user can only apply changes locally using the config editor within the app, whereas if set to `true` then changes can be written to disk directly through the UI. Defaults to `true`. Note that if authentication is enabled, the user must be of type `admin` in order to apply changes globally.
**`enableErrorReporting`** | `boolean` | _Optional_ | Enable reporting of unexpected errors and crashes. This is off by default, and **no data will ever be captured unless you explicitly enable it**. Turning on error reporting helps previously unknown bugs get discovered and fixed. Dashy uses [Sentry](https://github.com/getsentry/sentry) for error reporting. Defaults to `false`.
**`sentryDsn`** | `boolean` | _Optional_ | If you need to monitor errors in your instance, then you can use Sentry to collect and process bug reports. Sentry can be self-hosted, or used as SaaS, once your instance is setup, then all you need to do is pass in the DSN here, and enable error reporting. You can learn more on the [Sentry DSN Docs](https://docs.sentry.io/product/sentry-basics/dsn-explainer/). Note that this will only ever be used if `enableErrorReporting` is explicitly enabled.
**`disableSmartSort`** | `boolean` | _Optional_ | For the most-used and last-used app sort functions to work, a basic open-count is stored in local storage. If you do not want this to happen, then disable smart sort here, but you wil no longer be able to use these sort options. Defaults to `false`.
**`disableUpdateChecks`** | `boolean` | _Optional_ | If set to true, Dashy will not check for updates. Defaults to `false`.
**`enableServiceWorker`** | `boolean` | _Optional_ | Service workers cache web applications to improve load times and offer basic offline functionality, and are enabled by default in Dashy. The service worker can sometimes cause older content to be cached, requiring the app to be hard-refreshed. If you do not want SW functionality, or are having issues with caching, set this property to `true` to disable all service workers.
**`disableContextMenu`** | `boolean` | _Optional_ | If set to `true`, the custom right-click context menu will be disabled. Defaults to `false`.

**[⬆️ Back to Top](#configuring)**

### `appConfig.auth` _(optional)_
**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`users`** | `array` | _Optional_ | An array of objects containing usernames and hashed passwords. If this is not provided, then authentication will be off by default, and you will not need any credentials to access the app. See [`appConfig.auth.users`](#appconfigauthusers-optional). <br>**Note** this method of authentication is handled on the client side, so for security critical situations, it is recommended to use an [alternate authentication method](/docs/authentication.md#alternative-authentication-methods).
**`enableKeycloak`** | `object` | _Optional_ | If set to `true`, then authentication using Keycloak will be anabled. Note that you need to have an instance running, and have also configured `auth.keycloak`. Defaults to `false`
**`keycloak`** | `boolean` | _Optional_ | Config options to point Dashy to your Keycloak server. Requires `enableKeycloak: true`. See  [`auth.keycloak`](#appconfigauthkeycloak-optional) for more info
**`enableGuestAccess`** | `boolean` | _Optional_ | When set to `true`, an unauthenticated user will be able to access the dashboard, with read-only access, without having to login. Requires `auth.users` to be configured. Defaults to `false`.

For more info, see the **[Authentication Docs](/docs/authentication.md)**

**[⬆️ Back to Top](#configuring)**

### `appConfig.auth.users` _(optional)_

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`user`** | `string` | Required | Username to log in with
**`hash`** | `string` | Required | A SHA-256 hashed password
**`type`** | `string` | _Optional_ | The user type, either admin or normal

**[⬆️ Back to Top](#configuring)**

### `appConfig.auth.keycloak` _(optional)_

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`serverUrl`** | `string` | Required | The URL (or URL/ IP + Port) where your keycloak server is running
**`realm`** | `string` | Required | The name of the realm (must already be created) that you want to use
**`clientId`** | `string` | Required | The Client ID of the client you created for use with Dashy

**[⬆️ Back to Top](#configuring)**

### `appConfig.webSearch` _(optional)_

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`disableWebSearch`** | `string` | _Optional_ | Web search is enabled by default, but can be disabled by setting this property to `true`
**`searchEngine`** | `string` | _Optional_ | Set the key name for your search engine. Can also use a custom engine by setting this property to `custom`. Currently supported: `duckduckgo`, `google`, `whoogle`, `qwant`, `startpage`, `searx-bar` and `searx-info`. Defaults to `duckduckgo`
**`customSearchEngine`** | `string` | _Optional_ | You can also use a custom search engine, or your own self-hosted instance. This requires `searchEngine: custom` to be set. Then add the URL of your service, with GET query string included here
**`openingMethod`** | `string` | _Optional_ | Set your preferred opening method for search results: `newtab`, `sametab`, `workspace`. Defaults to `newtab`
**`searchBangs`** | `object` | _Optional_ | A key-value-pair set of custom search _bangs_ for redirecting query to a specific app or search engine. The key of each should be the bang you will type (typically starting with `/`, `!` or `:`), and value is the destination, either as a search engine key (e.g. `reddit`) or a URL with search parameters (e.g. `https://en.wikipedia.org/w/?search=`)


**[⬆️ Back to Top](#configuring)**

### `appConfig.hideComponents` _(optional)_

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`hideHeading`** | `boolean` | _Optional_ | If set to `true`, the page title & sub-title will not be visible. Defaults to `false`
**`hideNav`** | `boolean` | _Optional_ | If set to `true`, the navigation menu will not be visible. Defaults to `false`
**`hideSearch`** | `boolean` | _Optional_ | If set to `true`, the search bar will not be visible. Defaults to `false`
**`hideSettings`** | `boolean` | _Optional_ | If set to `true`, the settings menu will not be visible. Defaults to `false`
**`hideFooter`** | `boolean` | _Optional_ | If set to `true`, the footer will not be visible. Defaults to `false`
**`hideSplashScreen`** | `boolean` | _Optional_ | If set to `true`, splash screen will not be visible while the app loads. Defaults to `true` (except on first load, when the loading screen is always shown)

**[⬆️ Back to Top](#configuring)**

### `section`

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`name`** | `string` | Required | The title for the section
**`icon`** | `string` | _Optional_ | An single icon to be displayed next to the title. See [`section.icon`](#sectionicon-and-sectionitemicon)
**`items`** | `array` | Required | An array of items to be displayed within the section. See [`item`](#sectionitem)
**`displayData`** | `object` | _Optional_ | Meta-data to optionally overide display settings for a given section. See [`displayData`](#sectiondisplaydata-optional)

**[⬆️ Back to Top](#configuring)**

### `section.item`

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`title`** | `string` | Required | The text to display/ title of a given item. Max length `18`
**`description`** | `string` | _Optional_ | Additional info about an item, which is shown in the tooltip on hover, or visible on large tiles
**`url`** | `string` | Required | The URL / location of web address for when the item is clicked
**`icon`** | `string` | _Optional_ | The icon for a given item. Can be a font-awesome icon, favicon, remote URL or local URL. See [`item.icon`](#sectionicon-and-sectionitemicon)
**`target`** | `string` | _Optional_ | The opening method for when the item is clicked, either `newtab`, `sametab`, `modal` or `workspace`. Where `newtab` will open the link in a new tab, `sametab` will open it in the current tab, and `modal` will open a pop-up modal with the content displayed within that iframe. Note that for the iframe to load, you must have set the CORS headers to either allow `*` ot allow the domain that you are hosting Dashy on, for some websites and self-hosted services, this is already set.
**`hotkey`** | `number` | _Optional_ | Give frequently opened applications a numeric hotkey, between `0 - 9`. You can then just press that key to launch that application.
**`tags`** | `string[]` | _Optional_ | A list of tags, which can be used for improved search
**`statusCheck`** | `boolean` | _Optional_ | When set to `true`, Dashy will ping the URL associated with the current service, and display its status as a dot next to the item. The value here will override `appConfig.statusCheck` so you can turn off or on checks for a given service. Defaults to `appConfig.statusCheck`, falls back to `false`
**`statusCheckUrl`** | `string` | _Optional_ | If you've enabled `statusCheck`, and want to use a different URL to what is defined under the item, then specify it here
**`statusCheckHeaders`** | `object` | _Optional_ | If you're endpoint requires any specific headers for the status checking, then define them here 
**`statusCheckAllowInsecure`** | `boolean` | _Optional_ | By default, any request to insecure content will be blocked. Setting this option to `true` will disable the `rejectUnauthorized` option, enabling you to ping non-HTTPS services for the current item. Defaults to `false`
**`color`** | `string` | _Optional_ | An optional color for the text and font-awesome icon to be displayed in. Note that this will override the current theme and so may not display well
**`backgroundColor`** | `string` | _Optional_ | An optional background fill color for the that given item. Again, this will override the current theme and so might not display well against the background
**`provider`** | `string` | _Optional_ | The name of the provider for a given service, useful for when including hosted apps. In some themes, this is visible under the item name

**[⬆️ Back to Top](#configuring)**

### `section.displayData` _(optional)_

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`sortBy`** | `string` | _Optional_ | The sort order for items within the current section. By default items are displayed in the order in which they are listed in within the config. The following sort options are supported: `most-used` (most opened apps first), `last-used` (the most recently used apps), `alphabetical`, `reverse-alphabetical`, `random` and `default`
**`collapsed`** | `boolean` | _Optional_ | If true, the section will  be collapsed initially, and will need to be clicked to open. Useful for less regularly used, or very long sections. Defaults to `false` 
**`rows`** | `number` | _Optional_ | Height of the section, specified as the number of rows it should span vertically, e.g. `2`. Defaults to `1`. Max is `5`.
**`cols`** | `number` | _Optional_ | Width of the section, specified as the number of columns the section should span horizontally, e.g. `2`. Defaults to `1`. Max is `5`.
**`itemSize`** | `string` | _Optional_ | Specify the size for items within this group, either `small`, `medium` or `large`. Note that this will overide any settings specified through the UI
**`color`** | `string` | _Optional_ | A custom accent color for the section, as a hex code or HTML color (e.g. `#fff`)
**`customStyles`** | `string` | _Optional_ | Custom CSS properties that should be applied to that section, e.g. `border: 2px dashed #ff0000;`
**`sectionLayout`** | `string` | _Optional_ | Specify which CSS layout will be used to responsivley place items. Can be either `auto` (which uses flex layout), or `grid`. If `grid` is selected, then `itemCountX` and `itemCountY` may also be set. Defaults to `auto`
**`itemCountX`** | `number` | _Optional_ | The number of items to display per row / horizontally. If not set, it will be calculated automatically based on available space. Can only be set if `sectionLayout` is set to `grid`. Must be a whole number between `1` and `12`
**`itemCountY`** | `number` | _Optional_ | The number of items to display per column / vertically. If not set, it will be calculated automatically based on available space. If `itemCountX` is set, then `itemCountY` can be calculated automatically. Can only be set if `sectionLayout` is set to `grid`. Must be a whole number between `1` and `12`
**`hideForUsers`** | `string[]` | _Optional_ | Current section will be visible to all users, except for those specified in this list
**`showForUsers`** | `string[]` | _Optional_ | Current section will be hidden from all users, except for those specified in this list
**`hideForGuests`** | `boolean` | _Optional_ | Current section will be visible for logged in users, but not for guests (see `appConfig.enableGuestAccess`). Defaults to `false`

**[⬆️ Back to Top](#configuring)**

### `section.icon` and `section.item.icon`

**Field** | **Type** | **Required**| **Description**
--- | --- | --- | ---
**`icon`** | `string` | _Optional_ | The icon for a given item or section. See [Icon Docs](/docs/icons.md) for all available supported icon types. To auto-fetch icon from a services URL, aet to `favicon`. To use font-awesome, specify the category, followed by the icon name, e.g. `fas fa-rocket`, `fab fa-monero` or `fal fa-duck`. Similarly, for branded icons, you can use [simple-icons](https://simpleicons.org/) by setting icon to `si-[icon-name]` or [material-design-icons](https://dev.materialdesignicons.com/icons) by setting icon to `mdi-[icon-name]`. If set to `generative`, then a unique icon is generated from the apps URL or IP. You can also use hosted any by specifying it's URL, e.g. `https://i.ibb.co/710B3Yc/space-invader-x256.png`. To use a local image, first store it in `./public/item-icons/` (or `-v /app/public/item-icons/` in Docker) , and reference it by name and extension - e.g. set `image.png` to use `./public/item-icon/image.png`, you can also use sub-folders if you have a lot of icons, to keep them organised.

**[⬆️ Back to Top](#configuring)**

---

## Notes

### About YAML
If you're new to YAML, it's pretty straight-forward. The format is exactly the same as that of JSON, but instead of using curly braces, structure is denoted using whitespace. This [quick guide](https://linuxhandbook.com/yaml-basics/) should get you up to speed in a few minutes, for more advanced topics take a look at this [Wikipedia article](https://en.wikipedia.org/wiki/YAML).

### Config Saving Methods
When updating the config through the JSON editor in the UI, you have two save options: **Local** or **Write to Disk**.
- Changes saved locally will only be applied to the current user through the browser, and will not apply to other instances - you either need to use the cloud sync feature, or manually update the conf.yml file.
- On the other-hand, if you choose to write changes to disk, then your main `conf.yml` file will be updated, and changes will be applied to all users, and visible across all devices. For this functionality to work, you must be running Dashy with using the Docker container, or the Node server.  A backup of your current configuration will also be saved in the same directory. 

### Preventing Changes being Written to Disk
To disallow any changes from being written to disk via the UI config editor, set `appConfig.allowConfigEdit: false`. If you are using users, and have setup `auth` within Dashy, then only users with `type: admin` will be able to write config changes to disk.

### Example

```yaml
---
pageInfo:
  title: Home Lab
sections: # An array of sections
- name: Section 1 - Getting Started
  items: # An array of items
  - title: GitHub
    description: Source code and documentation on GitHub
    icon: fab fa-github
    url: https://github.com/Lissy93/dashy
  - title: Issues
    description: View currently open issues, or raise a new one
    icon: fas fa-bug
    url: https://github.com/Lissy93/dashy/issues
  - title: Demo
    description: A live demo
    icon: far fa-rocket
    url: https://dashy-demo-1.netlify.app 
- name: Section 2 - Local Services
  items:
  - title: Firewall
    icon: favicon
    url: http://192.168.1.1/
  - title: Game Server
    icon: https://i.ibb.co/710B3Yc/space-invader-x256.png
    url: http://192.168.130.1/
```

For more example config files, see: [this gist](https://gist.github.com/Lissy93/000f712a5ce98f212817d20bc16bab10)

If you need any help, feel free to [Raise an Issue](https://github.com/Lissy93/dashy/issues/new?assignees=Lissy93&labels=%F0%9F%A4%B7%E2%80%8D%E2%99%82%EF%B8%8F+Question&template=question.md&title=%5BQUESTION%5D) or [Start a Discussion](https://github.com/Lissy93/dashy/discussions)

Happy Configuring 🤓🔧

**[⬆️ Back to Top](#configuring)**

