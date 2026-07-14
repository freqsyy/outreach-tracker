# lane01 SCOUT - dry-run парсинга 3-х НОВЫХ источников (scout-2026-07-14-15)

- Дата прогона: 2026-07-14
- Команда: `python agent_scout.py --sources --sources-maxage 730`
- Режим: DRY-RUN (в БД НЕ писалось, только лог)
- Движок: `sites_sources.py` (новый модуль) + `--sources` в `agent_scout.py`

## ВАЖНО: DRIFT по источникам
Задача просит ProductHunt / BetaList / IndieHackers. Реально рабочие
(curl-верифицировано в scout-2026-07-14-09, возвращают 200 + серверный HTML/RSS):
- launchingnext (https://www.launchingnext.com/)  - серверный HTML ~30 доменов
- fazier        (https://fazier.com/)              - серверный HTML, много карточек
- hn_show       (https://news.ycombinator.com/rss) - RSS "Show HN"
Старальные из текста задачи:
- BetaList   -> 404 (мёртв)
- IndieHackers -> SPA, данные в JS, серверный HTML пустой
- ProductHunt -> уже есть в v2 (RSS /feed), повторно не берём
Поэтому реализовано ПОВЕРХ рабочих источников из спеки-09.

## Фильтры (применены в run_sources_dry)
- _is_mega(): сервисные домены (CDN/соц/аналитика/хостинг/shortlink/покер) +
  COMPANY_SUFFIX (по brand-части, БЕЗ TLD - иначе .app/.ai отсекались ложно)
- возраст <= N дней через RDAP (domain_age_days). max_age=730 => почти все
  пропускаются (фильтр не отсекает старые, так как порог высокий; для боевого
  запуска ставить max_age=120-180).
- upvotes: для этих источников в HTML/RSS upvotes НЕТ => всегда 0, порог
  (max_upvotes=10000) не срабатывает. Фильтр по апвоутам зарезервирован для
  будущих RSS-источников, где upvotes есть (напр. ProductHunt/PH-style).

## Итог прогона
- Всего кандидатов: 54
- launchingnext: 16
- fazier:        36
- hn_show:        2
- Утечек сервис-доменов (facebook/cloudflare/aws/github.io/...): 0

## Полный список кандидатов
fazier	adsbud.ai	age=None
fazier	ai-toolbox.ai	age=None
fazier	anglopeptides.ca	age=None
fazier	clawhost.pro	age=None
fazier	cybersecquiz.com	age=8
fazier	digiflower.net	age=40
fazier	doughrise.store	age=None
fazier	getsidekik.app	age=None
fazier	humanizeaitext.io	age=None
fazier	ideacrystal.com	age=54
fazier	innerveda.app	age=None
fazier	magicshot.ai	age=None
fazier	menuforma.com	age=68
fazier	miyawesim.app	age=None
fazier	nlh.poker	age=None
fazier	noodletomato.com	age=111
fazier	openfate.ai	age=None
fazier	pipelab.org	age=None
fazier	postnify.com	age=350
fazier	secureintent.ai	age=None
fazier	sevengrid.app	age=None
fazier	similartours.com	age=226
fazier	starterkitpro.com	age=521
fazier	tweetboost.ai	age=None
fazier	vectosolve.com	age=316
fazier	veritask.me	age=None
fazier	www.drizzlelemons.com	age=None
fazier	www.getforge.to	age=None
fazier	www.goahead.io	age=None
fazier	www.nexpept.ca	age=None
fazier	www.openparser.ai	age=None
fazier	www.planpoint.io	age=None
fazier	www.roverd.com	age=None
fazier	www.siliform.ai	age=None
fazier	yieldo.me	age=None
fazier	zoye.io	age=None
hn_show	hackney.app	age=None
hn_show	randofont.alesh.com	age=None
launchingnext	amadeusai.netlify.app	age=None
launchingnext	chatverse.io	age=None
launchingnext	creditrefresh.ai	age=None
launchingnext	fitaura.studio	age=None
launchingnext	hirehuddle.co	age=None
launchingnext	muzint.xyz	age=None
launchingnext	osaurus.ai	age=None
launchingnext	predictify.gg	age=None
launchingnext	sensorlinqapps.com	age=38
launchingnext	seosignal.app	age=None
launchingnext	slipnote.co	age=None
launchingnext	sukhsangeet.tech	age=None
launchingnext	uniqueweddingvenuesusa.com	age=44
launchingnext	www.pvavrt.com	age=None
launchingnext	www.shipos.app	age=None
launchingnext	www.signaldepth.com	age=None
