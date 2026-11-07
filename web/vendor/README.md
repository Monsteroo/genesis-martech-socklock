# web/vendor

Served by the app itself as `/vendor/<file>` (exact whitelist in `engine/web.py`). No CDN at runtime.

## lingui-core.mjs

| | |
|---|---|
| What | the real LinguiJS runtime `i18n` (`@lingui/core`) plus its ICU compiler `compileMessage` (`@lingui/message-utils`) |
| Version | `@lingui/core` **6.10.1** (released 2026-10-22), `@lingui/message-utils` **6.10.1** |
| Source | https://registry.npmjs.org/@lingui/core/-/core-6.10.1.tgz and https://registry.npmjs.org/@lingui/message-utils/-/message-utils-6.10.1.tgz (official npm packages, repository https://github.com/lingui/js-lingui) |
| npm integrity | core `sha512-s2b2yyXBwNFdKz5r3S9zaGLlJpSTH3+k+q0RGQr/TbjoTDsuTCMbHHT6stTWj377/4p/E3UTOrIy7r7J5jTGUA==`, message-utils `sha512-IzCtKG+wtC0uMFs7pONVAFscHzQN0FjW6RG7DULVJcHaLmH/hOGc7QpGX7Jzm7d8QguDfUM0QmSwFwLkYDmNvQ==` |
| sha256 of `lingui-core.mjs` | `520baa7eb4cfebf3ae282f15f9fccbbd6217096db83ab5893d9b0ba8d1b1b455` |
| License | MIT, see `LICENSE` (also covers the bundled `@messageformat/parser` 5.2.0, `@messageformat/date-skeleton` 1.1.0, `moo` 0.5.3) |

Why a bundle: since Lingui 4 the npm packages ship only ES modules with bare imports
(`@lingui/core` imports `@lingui/message-utils/compileMessage`, which imports `@messageformat/parser` and
`@messageformat/date-skeleton`). A browser cannot load that without an import map plus several more files, and there is
no UMD/browser file in the package. So one reproducible bundling step turns them into a single ES module.
Nothing is rewritten: the file is the unmodified package code, minified, with `process.env.NODE_ENV` set to `production`.

Exports: `i18n`, `setupI18n`, `I18n`, `compileMessage`. The page does
`const L = await import("/vendor/lingui-core.mjs"); L.i18n.setMessagesCompiler(L.compileMessage);`.

### Reproduce (one-time, not needed to run the app)

```
cd vendor-src/lingui
npm ci --ignore-scripts          # exact versions from package-lock.json (esbuild 0.28.2 is the only build tool)
npm run build                    # writes ../../web/vendor/lingui-core.mjs
shasum -a 256 ../../web/vendor/lingui-core.mjs
```

Checked when vendoring: the `dist/` of both packages from `npm pack` is byte-identical to the one installed from the
lockfile, and the lockfile integrity values equal the registry's `dist.integrity`. Update = change the versions in
`vendor-src/lingui/package.json`, rebuild, update this file.
