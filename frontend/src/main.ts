import './style.css';

type Source = { source_id: number; name: string; provider: string } | null;
type Site = { site_id: string; name: string; primary_domain: string; timezone: string };
type Opportunity = {
  page_id: number; canonical_url: string; page_type: string; business_priority: number;
  clicks: number | null; impressions: number | null; normal_inquiries: number | null;
  primary_keyword: string | null; keyword_clicks: number | null; keyword_impressions: number | null;
  position: number | null; volume: number | null; matched_presets: string[];
  attention_level: 'high' | 'medium' | 'normal'; missing_inputs: string[];
  traffic_tier: string; lead_tier: string; click_trend: string; rank_trend: string; rank_band: string;
};
type Overview = { month: string | null; geo: string; total: number; summary: {
  observed_pages: number; search_clicks: number | null; search_impressions: number | null;
  normal_inquiries: number | null;
}; search_source: Source; inquiry_source: Source };
type OpportunityResponse = { month: string | null; partial_month?: boolean; geo: string; demand_geo: string;
  total: number; rows: Opportunity[]; sources: Record<string, Source> };
type Preset = { id: string; name: string; level: string; count: number };
type SiteSettings = { default_geo: string; default_demand_geo: string; language_filter: string | null;
  page_type_patterns: Array<{ pattern: string; type: string }>;
  intent_terms: string[]; product_query_exclude_terms: string[]; rules: Record<string, number> };
type ImportPreview = { columns: string[]; row_count: number; sample: Record<string, unknown>[] };

const app = document.querySelector<HTMLDivElement>('#app')!;
const state = {
  siteId: localStorage.getItem('seo-site') || '', month: '', geo: 'GLOBAL', demandGeo: 'US',
  preset: '', pageType: '', search: '', offset: 0, searchSource: '', querySource: '',
  demandSource: '', inquirySource: '', sites: [] as Site[], settings: null as SiteSettings | null,
  pageId: null as number | null, preview: null as ImportPreview | null, presets: [] as Preset[],
  facets: {} as Record<string,string[]>,
};
const facetOptions: Record<string, Array<[string,string]>> = {
  traffic_tier:[['high','高搜索流量'],['low','低搜索流量'],['none','无搜索点击'],['unknown','无流量数据']],
  lead_tier:[['high','高关联询盘'],['low','低关联询盘'],['none','无记录询盘'],['unknown','无询盘数据']],
  click_trend:[['strong_down','点击明显下降'],['slight_down','点击小幅下降'],['stable','点击稳定'],['slight_up','点击小幅上升'],['strong_up','点击明显上升'],['unknown','无趋势数据']],
  rank_trend:[['strong_down','主词排名明显下降'],['slight_down','主词排名小幅下降'],['stable','主词排名稳定'],['slight_up','主词排名小幅上升'],['strong_up','主词排名明显上升'],['unknown','无趋势数据']],
  rank_band:[['top5','前 5'],['first_page','首页范围'],['second_page','第二页范围'],['later','排名靠后'],['unknown','无有效主词']],
};
const facetGroupNames: Record<string,string> = {traffic_tier:'页面流量',lead_tier:'关联询盘',click_trend:'页面点击',rank_trend:'主词排名变化',rank_band:'排名区间'};
const facetLabel = (group: string, value: string) => facetOptions[group]?.find(x => x[0] === value)?.[1] || value;
const $ = <T extends HTMLElement = HTMLElement>(selector: string) => document.querySelector<T>(selector)!;
const esc = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const nf = (value: number | null | undefined) => value === null || value === undefined ? '—' : Intl.NumberFormat().format(value);
const pf = (value: number | null | undefined) => value === null || value === undefined ? '—' : value.toFixed(1);
const delta = (value: number | null | undefined, rank = false) => value === null || value === undefined ? '' :
  `<small class="muted">较上月 ${value > 0 ? '+' : ''}${rank ? value.toFixed(1) : nf(value)}${rank && value !== 0 ? (value < 0 ? ' ↑' : ' ↓') : ''}</small>`;
const qs = (values: Record<string, string | number | null | undefined>) => new URLSearchParams(Object.entries(values)
  .filter(([, value]) => value !== null && value !== undefined && value !== '') as [string,string][]).toString();
const sitePath = (suffix: string) => `/api/sites/${encodeURIComponent(state.siteId)}${suffix}`;

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.detail || `HTTP ${response.status}`);
  return body as T;
}
function notice(message: string, bad = false) {
  const target = $('#notice'); target.textContent = message; target.className = bad ? 'notice bad' : 'notice';
  setTimeout(() => { if (target.textContent === message) target.textContent = ''; }, 6000);
}
function showDialog(id: string) { $(id).querySelector('dialog')?.showModal(); }
function closeDialog(id: string) { $(id).querySelector('dialog')?.close(); }
function sourceLabel(source: Source) { return source ? source.name : '未接入'; }
function pathLabel(url: string) { try { return new URL(url).pathname; } catch { return url; } }

function renderShell() {
  app.innerHTML = `
    <header class="topbar"><div class="brand"><span class="mark">◇</span><div><strong>Open SEO Workbench</strong><small>多站点 · 多来源 · 可解释</small></div></div>
      <div class="top-actions"><select id="siteSelect" aria-label="选择站点"></select><button id="createSiteBtn" class="button">+ 站点</button><button id="importBtn" class="button">导入数据</button><button id="settingsBtn" class="button">站点设置</button></div></header>
    <main><section class="hero"><div><div class="eyebrow">SITE INTELLIGENCE</div><h1 id="siteTitle">选择一个站点</h1><p id="siteSubtitle">页面、关键词、来源、地区与询盘在同一个视图中核对。</p></div><div class="hero-geo" id="provenance"></div></section>
      <div id="notice" class="notice"></div><section id="emptyState" class="empty-state hidden"><h2>从第一个站点开始</h2><p>创建站点后导入搜索表现和关键词数据。缺少某个来源时，页面仍可查看。</p><button class="button primary" id="emptyCreate">创建站点</button></section>
      <div id="workspace" class="hidden"><section class="stats" id="stats"></section>
        <section class="panel"><div class="panel-head"><div><h2>优先处理组合</h2><p>每项都能下钻到页面、关键词和来源证据。缺数据的规则不会把未知当作零。</p></div></div><div class="presets" id="presets"></div></section>
        <section class="panel"><div class="panel-head"><div><h2>页面机会</h2><p>红色优先处理，黄色进一步核查，灰色为常规或数据不足。</p></div><span id="pageCount" class="muted"></span></div>
          <div class="filters"><label>月份<input id="month" type="month" /></label><label>搜索地区<select id="geo"><option value="GLOBAL">全球</option><option value="US">美国</option></select></label>
            <label>搜索量地区<select id="demandGeo"><option value="US">美国</option><option value="GLOBAL">全球</option></select></label>
            <label>页面类型<select id="pageType"><option value="">全部类型</option><option value="product">产品</option><option value="category">分类</option><option value="blog">Blog</option><option value="home">首页</option><option value="other">其他</option></select></label>
            <label>URL 搜索<input id="search" placeholder="输入路径或词" /></label><button class="button" id="facetBtn">多标签筛选</button><button class="button" id="sourceToggle">数据来源 ▾</button></div>
          <div class="facet-summary" id="facetSummary"></div>
          <div class="sources hidden" id="sources"><label>搜索页面来源<select id="searchSource"></select></label><label>词×页面来源<select id="querySource"></select></label><label>搜索量来源<select id="demandSource"></select></label><label>询盘来源<select id="inquirySource"></select></label><button class="button" id="saveSources">设为站点默认</button></div>
          <div class="table-scroll"><table><thead><tr><th>处理级别 / 页面</th><th>类型</th><th>主词</th><th>页面点击</th><th>主词展示</th><th>平均排名</th><th>月搜索量</th><th>回看期 NORMAL 落地</th><th>命中组合</th></tr></thead><tbody id="pageRows"></tbody></table></div>
          <div class="pager"><button class="button" id="prev">上一页</button><span id="pagerText"></span><button class="button" id="next">下一页</button></div></section>
      </div></main>
    <aside id="detail" class="detail hidden"><button class="icon-button" id="closeDetail" aria-label="关闭">×</button><div id="detailContent"></div></aside><div id="shade" class="shade hidden"></div>
    <div id="siteModal"><dialog><form id="siteForm"><h2>创建站点</h2><label>站点名称<input name="name" required placeholder="Example Store" /></label><label>主域名<input name="domain" required placeholder="example.com" /></label><label>时区<input name="timezone" value="UTC" /></label><div class="actions"><button type="button" class="button" data-close="siteModal">取消</button><button class="button primary">创建</button></div></form></dialog></div>
    <div id="importModal"><dialog class="wide"><h2>导入站点数据</h2><p class="muted">选择数据类型并映射列名。默认追加或更新；只有选择“完整范围”才会替换相同来源、地区和时间范围的数据。</p>
      <div class="form-grid"><label>文件<input id="importFile" type="file" accept=".csv,.tsv,.xlsx,.json" /></label><label>数据类型<select id="dataset"><option value="search_page">页面搜索表现</option><option value="search_page_keyword">词 × 页面搜索表现</option><option value="keyword_demand">关键词搜索量</option><option value="inquiry">询盘</option><option value="custom_metric">自定义指标</option></select></label>
        <label>来源名称<input id="sourceName" placeholder="GSC 导出 / 其他供应商" /></label><label>地区<select id="importGeo"><option value="GLOBAL">全球</option><option value="US">美国</option></select></label><label>粒度<select id="grain"><option value="month">月</option><option value="day">日</option></select></label><label>默认月份<input id="importPeriod" type="month" /></label><label>覆盖方式<select id="coverage"><option value="partial">追加 / 更新</option><option value="complete">完整范围替换</option></select></label></div>
      <label>列名映射 JSON<textarea id="mapping" rows="3" placeholder='{"page_url":"Page","keyword":"Query","period":"Month"}'>{}</textarea></label><div id="importPreview" class="preview"></div>
      <div class="actions"><button class="button" data-close="importModal">取消</button><button class="button" id="previewBtn">预览列</button><button class="button primary" id="uploadBtn">导入</button></div></dialog></div>
    <div id="settingsModal"><dialog class="wide"><h2>站点设置</h2><p class="muted">URL 分类和采购词按站点配置。指标定义可通过 API 或 MCP 扩展。</p>
      <div class="form-grid"><label>默认搜索地区<select id="settingGeo"><option value="GLOBAL">全球</option><option value="US">美国</option></select></label><label>默认搜索量地区<select id="settingDemandGeo"><option value="GLOBAL">全球</option><option value="US">美国</option></select></label></div><label>语言筛选<input id="settingLanguage" placeholder="留空表示所有语言" /></label>
      <label>采购意图词（每行一个）<textarea id="intentTerms" rows="5"></textarea></label><label>产品主词排除的资讯词（每行一个）<textarea id="excludedTerms" rows="4"></textarea></label><label>页面类型路径规则 JSON<textarea id="patterns" rows="7"></textarea></label>
      <div id="ruleFields" class="rule-fields"></div><div class="actions"><button class="button" data-close="settingsModal">取消</button><button class="button primary" id="saveSettings">保存设置</button></div></dialog></div>
    <div id="facetModal"><dialog class="wide"><h2>多标签筛选</h2><p class="muted">同类选中任一项，跨类同时满足。</p><div id="facetGroups"></div><div class="actions"><button class="button" id="clearFacets">清空</button><button class="button" data-close="facetModal">取消</button><button class="button primary" id="applyFacets">应用</button></div></dialog></div>`;
  bindShell();
}

function bindShell() {
  $('#createSiteBtn').addEventListener('click', () => showDialog('#siteModal'));
  $('#emptyCreate').addEventListener('click', () => showDialog('#siteModal'));
  $('#importBtn').addEventListener('click', () => showDialog('#importModal'));
  $('#settingsBtn').addEventListener('click', openSettings);
  $('#facetBtn').addEventListener('click', openFacets);
  $('#clearFacets').onclick = () => { state.facets = {}; closeDialog('#facetModal'); state.offset = 0; void refresh(); };
  $('#applyFacets').onclick = () => { state.facets = {}; document.querySelectorAll<HTMLInputElement>('[data-facet]:checked').forEach(input => { (state.facets[input.dataset.facet!] ||= []).push(input.value); }); closeDialog('#facetModal'); state.offset = 0; void refresh(); };
  document.querySelectorAll<HTMLElement>('[data-close]').forEach(button => button.onclick = () => closeDialog('#' + button.dataset.close));
  $('#siteSelect').addEventListener('change', async event => { state.siteId = (event.target as HTMLSelectElement).value; localStorage.setItem('seo-site', state.siteId); await loadSite(); });
  $('#siteForm').addEventListener('submit', async event => {
    event.preventDefault(); const form = new FormData(event.target as HTMLFormElement);
    try { const site = await api<Site>('/api/sites', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(Object.fromEntries(form)) });
      state.siteId = site.site_id; localStorage.setItem('seo-site', state.siteId); closeDialog('#siteModal'); await loadSites(); await loadSite(); notice('站点已创建');
    } catch (error) { notice(String(error), true); }
  });
  const refreshFields = ['month','geo','demandGeo','pageType','search','searchSource','querySource','demandSource','inquirySource'];
  refreshFields.forEach(id => $('#' + id).addEventListener(id === 'search' ? 'input' : 'change', () => {
    const key = id as keyof typeof state; (state as unknown as Record<string, string>)[key] = ($<HTMLInputElement>('#' + id)).value;
    state.offset = 0;
    if (id === 'geo') { state.searchSource = ''; state.querySource = ''; state.inquirySource = ''; void loadSources().then(refresh); }
    else if (id === 'demandGeo') { state.demandSource = ''; void loadSources().then(refresh); }
    else void refresh();
  }));
  $('#sourceToggle').onclick = () => $('#sources').classList.toggle('hidden');
  $('#saveSources').onclick = () => void saveSourcePreferences();
  $('#prev').onclick = () => { state.offset = Math.max(0, state.offset - 100); void refresh(); };
  $('#next').onclick = () => { state.offset += 100; void refresh(); };
  $('#closeDetail').onclick = closeDetail; $('#shade').onclick = closeDetail;
  $('#pageRows').addEventListener('click', event => {
    const button = (event.target as HTMLElement).closest<HTMLElement>('[data-page]');
    if (button) void openPage(Number(button.dataset.page));
  });
  $('#presets').addEventListener('click', event => {
    const button = (event.target as HTMLElement).closest<HTMLElement>('[data-preset]');
    if (button) { state.preset = state.preset === button.dataset.preset ? '' : button.dataset.preset || ''; state.offset = 0; void refresh(); }
  });
  $('#previewBtn').onclick = () => void previewImport(); $('#uploadBtn').onclick = () => void uploadImport();
  $('#saveSettings').onclick = () => void saveSettings();
}

async function loadSites() {
  state.sites = await api<Site[]>('/api/sites');
  $('#siteSelect').innerHTML = state.sites.map(s => `<option value="${esc(s.site_id)}">${esc(s.name)} · ${esc(s.primary_domain)}</option>`).join('');
  if (!state.sites.some(s => s.site_id === state.siteId)) state.siteId = state.sites[0]?.site_id || '';
  ($<HTMLSelectElement>('#siteSelect')).value = state.siteId;
  $('#emptyState').classList.toggle('hidden', !!state.siteId);
  $('#workspace').classList.toggle('hidden', !state.siteId);
}

async function loadSite() {
  if (!state.siteId) return;
  const site = state.sites.find(s => s.site_id === state.siteId)!;
  state.settings = await api<SiteSettings>(sitePath('/settings'));
  state.month = ''; state.geo = state.settings.default_geo || 'GLOBAL'; state.demandGeo = state.settings.default_demand_geo || 'GLOBAL';
  state.preset = ''; state.pageType = ''; state.search = ''; state.offset = 0;
  state.facets = {};
  state.searchSource = ''; state.querySource = ''; state.demandSource = ''; state.inquirySource = '';
  $('#siteTitle').textContent = site.name;
  $('#siteSubtitle').textContent = `${site.primary_domain} · ${site.timezone} · 每项指标都显示来源与地区`;
  ($<HTMLSelectElement>('#geo')).value = state.geo;
  ($<HTMLSelectElement>('#demandGeo')).value = state.demandGeo;
  ($<HTMLInputElement>('#search')).value = '';
  ($<HTMLSelectElement>('#pageType')).value = '';
  await loadSources(); await refresh();
}

async function loadSources() {
  const data = await api<{ sources: Array<Source & { source_id: number; name: string }>; availability: Array<{dataset:string;source_id:number;geo:string}> }>(sitePath('/sources'));
  for (const [id, dataset, geo] of [['searchSource','search_page',state.geo],['querySource','search_page_keyword',state.geo],
                                   ['demandSource','keyword_demand',state.demandGeo],['inquirySource','inquiry',state.geo]]) {
    const ids = new Set(data.availability.filter(x => x.dataset === dataset && x.geo === geo).map(x => x.source_id));
    const options = data.sources.filter(x => ids.has(x.source_id));
    $('#' + id).innerHTML = '<option value="">自动选择</option>' + options.map(x => `<option value="${esc(x.name)}">${esc(x.name)}</option>`).join('');
    ($<HTMLSelectElement>('#' + id)).value = (state as unknown as Record<string,string>)[id] || '';
  }
}

async function refresh() {
  if (!state.siteId) return;
  try {
    const overview = await api<Overview>(sitePath('/pages?' + qs({ month:state.month, geo:state.geo, search_source:state.searchSource, inquiry_source:state.inquirySource, limit:1 })));
    if (!state.month && overview.month) { state.month = overview.month; ($<HTMLInputElement>('#month')).value = state.month; }
    const [data, presets] = await Promise.all([
      api<OpportunityResponse>(sitePath('/opportunities?' + qs({ month:state.month, geo:state.geo,
        demand_geo:state.demandGeo, search_source:state.searchSource, query_source:state.querySource,
        demand_source:state.demandSource, inquiry_source:state.inquirySource, preset:state.preset,
        page_type:state.pageType, search:state.search, facets:JSON.stringify(state.facets), limit:100, offset:state.offset }))),
      api<Preset[]>(sitePath('/presets?' + qs({ month:state.month, geo:state.geo, demand_geo:state.demandGeo,
        search_source:state.searchSource, query_source:state.querySource, demand_source:state.demandSource,
        inquiry_source:state.inquirySource }))),
    ]);
    renderStats(overview); renderPresets(presets); renderPages(data);
    $('#facetSummary').textContent = Object.entries(state.facets).flatMap(([group,values]) => values.map(value => facetLabel(group,value))).join(' · ') || '未筛选页面标签';
    $('#provenance').innerHTML = `<strong>${esc(state.geo)} 搜索表现</strong><span>${esc(sourceLabel(data.sources.search))}</span><strong>${esc(state.demandGeo)} 搜索量</strong><span>${esc(sourceLabel(data.sources.demand))}</span>`;
    await loadSources();
  } catch (error) { notice(String(error), true); }
}

function renderStats(data: Overview) {
  const items = [
    ['已观察页面', nf(data.summary.observed_pages), '有搜索页面记录'],
    ['搜索点击', nf(data.summary.search_clicks), sourceLabel(data.search_source)],
    ['搜索展示', nf(data.summary.search_impressions), state.geo + ' 地区'],
    ['NORMAL 落地询盘', nf(data.summary.normal_inquiries), sourceLabel(data.inquiry_source)],
  ];
  $('#stats').innerHTML = items.map(x => `<div class="stat"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong><small>${esc(x[2])}</small></div>`).join('');
}

function renderPresets(items: Preset[]) {
  state.presets = items;
  $('#presets').innerHTML = items.map(x => `<button class="preset ${state.preset === x.id ? 'active' : ''}" data-preset="${esc(x.id)}"><strong>${esc(x.name)}</strong><span>${nf(x.count)} 页 · ${x.level === 'high' ? '优先处理' : x.level === 'medium' ? '进一步核查' : '转化排查'}</span></button>`).join('');
}

function renderPages(data: OpportunityResponse) {
  $('#pageCount').textContent = `${nf(data.total)} 个页面${data.partial_month ? ' · 当月未结束，暂停环比趋势' : ''}`;
  $('#pageRows').innerHTML = data.rows.length ? data.rows.map(row => `<tr class="attention-${row.attention_level}"><td><button class="page-link" data-page="${row.page_id}"><span class="priority ${row.attention_level}">${row.attention_level === 'high' ? '高' : row.attention_level === 'medium' ? '中' : '常规'}</span>${esc(pathLabel(row.canonical_url))}</button><small class="muted">#${row.page_id}${row.missing_inputs.length ? ' · 缺 ' + esc(row.missing_inputs.join('、')) : ''}</small><small class="muted">${esc([facetLabel('traffic_tier',row.traffic_tier),facetLabel('lead_tier',row.lead_tier),facetLabel('rank_band',row.rank_band)].join(' · '))}</small></td>
    <td>${esc(row.page_type)}</td><td>${esc(row.primary_keyword || '—')}</td><td class="number">${nf(row.clicks)}</td><td class="number">${nf(row.keyword_impressions)}</td><td class="number">${pf(row.position)}</td><td class="number">${nf(row.volume)}</td><td class="number">${nf(row.normal_inquiries)}</td><td>${row.matched_presets.length ? row.matched_presets.map(x => `<span class="tag">${esc(state.presets.find(p => p.id === x)?.name || x)}</span>`).join('') : '<span class="muted">证据不足或未命中</span>'}</td></tr>`).join('') : '<tr><td colspan="9" class="no-results">当前条件下没有页面</td></tr>';
  $('#pagerText').textContent = `${data.total ? state.offset + 1 : 0}–${Math.min(state.offset + 100, data.total)} / ${nf(data.total)}`;
  ($<HTMLButtonElement>('#prev')).disabled = state.offset === 0;
  ($<HTMLButtonElement>('#next')).disabled = state.offset + 100 >= data.total;
}

async function openPage(pageId: number, offset = 0, limit = 10) {
  state.pageId = pageId; $('#detail').classList.remove('hidden'); $('#shade').classList.remove('hidden');
  $('#detailContent').innerHTML = '<p>加载中…</p>';
  try {
    const data = await api<any>(sitePath(`/pages/${pageId}?` + qs({ month:state.month, geo:state.geo,
      demand_geo:state.demandGeo, search_source:state.searchSource, query_source:state.querySource,
      demand_source:state.demandSource, inquiry_source:state.inquirySource, limit, offset })));
    const metric = data.metrics || {};
    $('#detailContent').innerHTML = `<div class="eyebrow">PAGE #${pageId}</div><h2>${esc(pathLabel(data.page.canonical_url))}</h2><a href="${esc(data.page.canonical_url)}" target="_blank" rel="noopener noreferrer">${esc(data.page.canonical_url)}</a>
      <div class="detail-cards"><div><span>搜索点击</span><strong>${nf(metric.clicks)}</strong></div><div><span>展示</span><strong>${nf(metric.impressions)}</strong></div><div><span>隐藏查询点击</span><strong>${nf(data.hidden_query_clicks)}</strong></div></div>
      <p class="source-note">页面 ${esc(sourceLabel(data.search_source))} / 查询 ${esc(sourceLabel(data.query_source))} · ${esc(data.geo)}；搜索量 ${esc(sourceLabel(data.demand_source))} · ${esc(data.demand_geo)}。不同来源的点击不能直接相减。</p>
      <h3>该页面的可见关键词 · ${nf(data.keyword_count)} 个</h3><p class="muted">默认前十按点击、展示排序；可翻页查看全部可见词。变化按同一词、同一页面、同一来源和地区比较。</p><div class="table-scroll"><table><thead><tr><th>关键词</th><th>点击 / 变化</th><th>展示 / 变化</th><th>CTR</th><th>平均排名 / 变化</th><th>月搜索量</th></tr></thead><tbody>${data.keywords.map((w: any) => `<tr><td>${esc(w.query)}</td><td>${nf(w.clicks)}${delta(w.change?.clicks)}</td><td>${nf(w.impressions)}${delta(w.change?.impressions)}</td><td>${w.ctr === null ? '—' : (w.ctr * 100).toFixed(2) + '%'}</td><td>${pf(w.position)}${delta(w.change?.position, true)}</td><td>${nf(w.demand?.volume)}<small class="muted">${esc(w.demand?.snapshot_month || '')}</small></td></tr>`).join('') || '<tr><td colspan="6">该月没有可见关键词记录</td></tr>'}</tbody></table></div>
      <div class="pager"><button class="button" id="wordPrev" ${offset === 0 ? 'disabled' : ''}>上一批</button><span>${data.keyword_count ? offset + 1 : 0}–${Math.min(offset + limit, data.keyword_count)} / ${nf(data.keyword_count)}</span><button class="button" id="wordNext" ${offset + limit >= data.keyword_count ? 'disabled' : ''}>下一批</button><button class="button" id="wordSize">${limit === 10 ? '每批 100 个' : '前十视图'}</button></div>
      <h3>询盘月度记录</h3><div class="table-scroll"><table><thead><tr><th>月份</th><th>NORMAL 落地</th></tr></thead><tbody>${data.inquiries_by_month.map((x: any) => `<tr><td>${esc(x.month)}</td><td>${nf(x.count)}</td></tr>`).join('') || '<tr><td colspan="2">未接入或无已记录询盘</td></tr>'}</tbody></table></div>`;
    $('#wordPrev').onclick = () => void openPage(pageId, Math.max(0, offset - limit), limit);
    $('#wordNext').onclick = () => void openPage(pageId, offset + limit, limit);
    $('#wordSize').onclick = () => void openPage(pageId, 0, limit === 10 ? 100 : 10);
  } catch (error) { $('#detailContent').textContent = String(error); }
}
function closeDetail() { state.pageId = null; $('#detail').classList.add('hidden'); $('#shade').classList.add('hidden'); }

async function previewImport() {
  const file = ($<HTMLInputElement>('#importFile')).files?.[0]; if (!file) { notice('先选择文件', true); return; }
  try { state.preview = await api<ImportPreview>('/api/import-preview?' + qs({filename:file.name}), { method:'POST', body:file });
    $('#importPreview').innerHTML = `<strong>${nf(state.preview.row_count)} 行</strong><p>列：${state.preview.columns.map(esc).join('、')}</p><pre>${esc(JSON.stringify(state.preview.sample, null, 2))}</pre>`;
  } catch (error) { notice(String(error), true); }
}
async function uploadImport() {
  const file = ($<HTMLInputElement>('#importFile')).files?.[0]; if (!file || !state.siteId) { notice('先选择站点和文件', true); return; }
  try {
    const mapping = JSON.parse(($<HTMLTextAreaElement>('#mapping')).value);
    const dataset = ($<HTMLSelectElement>('#dataset')).value;
    const result = await api<{ row_count: number; duplicate: boolean }>(sitePath(`/imports/${dataset}?` + qs({ filename:file.name,
      source_name:($<HTMLInputElement>('#sourceName')).value, geo:($<HTMLSelectElement>('#importGeo')).value,
      grain:($<HTMLSelectElement>('#grain')).value, default_period:($<HTMLInputElement>('#importPeriod')).value,
      coverage:($<HTMLSelectElement>('#coverage')).value })),
      { method:'POST', headers:{ 'X-Column-Mapping':JSON.stringify(mapping) }, body:file });
    closeDialog('#importModal'); notice(result.duplicate ? '这份文件已导入，未重复计数' : `已导入 ${nf(result.row_count)} 行`);
    await loadSources(); await refresh();
  } catch (error) { notice(String(error), true); }
}

function openSettings() {
  const settings = state.settings; if (!settings) return;
  ($<HTMLSelectElement>('#settingGeo')).value = settings.default_geo;
  ($<HTMLSelectElement>('#settingDemandGeo')).value = settings.default_demand_geo;
  ($<HTMLInputElement>('#settingLanguage')).value = settings.language_filter || '';
  ($<HTMLTextAreaElement>('#intentTerms')).value = settings.intent_terms.join('\n');
  ($<HTMLTextAreaElement>('#excludedTerms')).value = (settings.product_query_exclude_terms || []).join('\n');
  ($<HTMLTextAreaElement>('#patterns')).value = JSON.stringify(settings.page_type_patterns, null, 2);
  const names: Record<string,string> = {primary_min_impressions:'主词最低展示',min_impressions:'机会词最低展示',min_volume:'月搜索量门槛',product_max_clicks:'产品机会页最多点击',low_ctr:'低 CTR 上限',min_click_loss:'每月最少损失点击',lead_lookback_days:'询盘回看天数',traffic_high_product:'产品高流量点击',traffic_high_category:'分类高流量点击',traffic_high_blog:'Blog 高流量点击',traffic_high_other:'其他高流量点击',lead_high_count:'高关联询盘条数',click_slight_delta:'点击小幅变化',click_strong_delta:'点击明显变化',rank_slight_delta:'排名小幅变化',rank_strong_delta:'排名明显变化'};
  $('#ruleFields').innerHTML = Object.entries(settings.rules).map(([key, value]) => `<label>${esc(names[key] || key)}<input type="number" step="any" data-rule="${esc(key)}" value="${value}" /></label>`).join('');
  showDialog('#settingsModal');
}
function openFacets() {
  $('#facetGroups').innerHTML = Object.entries(facetOptions).map(([group,options]) => `<fieldset class="facet-group"><legend>${esc(facetGroupNames[group])}</legend>${options.map(([value,label]) => `<label><input type="checkbox" data-facet="${esc(group)}" value="${esc(value)}" ${(state.facets[group] || []).includes(value) ? 'checked' : ''} /> ${esc(label)}</label>`).join('')}</fieldset>`).join('');
  showDialog('#facetModal');
}
async function saveSettings() {
  try {
    const rules = Object.fromEntries([...document.querySelectorAll<HTMLInputElement>('[data-rule]')].map(input => [input.dataset.rule!, Number(input.value)]));
    const values = { default_geo:($<HTMLSelectElement>('#settingGeo')).value,
      default_demand_geo:($<HTMLSelectElement>('#settingDemandGeo')).value,
      language_filter:($<HTMLInputElement>('#settingLanguage')).value || null,
      intent_terms:($<HTMLTextAreaElement>('#intentTerms')).value.split('\n').map(x => x.trim()).filter(Boolean),
      product_query_exclude_terms:($<HTMLTextAreaElement>('#excludedTerms')).value.split('\n').map(x => x.trim()).filter(Boolean),
      page_type_patterns:JSON.parse(($<HTMLTextAreaElement>('#patterns')).value), rules };
    state.settings = await api<SiteSettings>(sitePath('/settings'), { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({values}) });
    closeDialog('#settingsModal'); notice('站点设置已保存'); await refresh();
  } catch (error) { notice(String(error), true); }
}
async function saveSourcePreferences() {
  const selected: Array<[string,string,string]> = [
    ['search_clicks',state.geo,state.searchSource],['search_position',state.geo,state.querySource],
    ['keyword_volume',state.demandGeo,state.demandSource],['normal_inquiries',state.geo,state.inquirySource],
  ].filter((x): x is [string,string,string] => !!x[2]);
  try { for (const [metric_key,geo,source] of selected) await api(sitePath('/source-preferences'),
      { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({metric_key,geo,sources:[source]}) });
    notice(selected.length ? '已保存所选来源为默认' : '选择来源后再保存');
  } catch (error) { notice(String(error), true); }
}

renderShell();
loadSites().then(loadSite).catch(error => notice(String(error), true));
