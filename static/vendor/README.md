# static/vendor — сторонні файли, які сайт віддає сам (без CDN)

| Файл | Звідки | Ліцензія | sha256 |
|---|---|---|---|
| `rrweb-record-2.1.7.min.js` | npm `@rrweb/record@2.1.7`, `umd/record.min.js` | MIT | `790f5c56aee0a1f501e629210bed86b1bae73d2a7cf29ca6c4295407cd6bdc96` |
| `rrweb-player-2.1.7.min.js` | npm `rrweb-player@2.1.7`, `umd/rrweb-player.min.js` | MIT | `c55944a8e8ab441a72eb7898bafaa1198c4dc42ea5f7c95111c5db7e5c91ba32` |
| `rrweb-player-2.1.7.min.css` | npm `rrweb-player@2.1.7`, `dist/style.min.css` | MIT | `ce5e96298e20e7262e317ef8d51aacd8e75cd14cfa66aaff43ceecbb7a3e132a` |

Рядок `//# sourceMappingURL=…` прибрано (карт поруч немає). Запис вантажить `static/analytics.js`
ліниво, програвач — лише панель `/stats/replay/…`. Оновлення: `npm pack <пакет>@<версія>`,
скопіювати файл під новим ім'ям з версією, поправити шлях у `analytics.js` /
`analytics/templates/stats_replay.html` і цю таблицю.
