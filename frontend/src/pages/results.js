import {store} from '../stores/research.js';
import {runs} from '../api/research.js';
import {esc,fmt} from '../utils/format.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {runList} from '../components/common/run-list.js';
import {publicationNote} from '../components/common/publication.js';
import {download} from '../utils/download.js';
function provenance(r){
 if(!r.snapshot?.some(x=>x.provenance?.length))return '';
 const rows=[];
 for(const item of r.snapshot){for(const p of item.provenance||[]){rows.push([p.study_name||p.study_id,p.source_key,p.revision?'v'+p.revision:'—',p.updated_at||'—',p.change_reason||'—']);}}
 return `<h2>底层来源与更新原因</h2><p>联合研究的计数按底层来源去重后汇总；下列为参与本次冻结的来源记录。</p>${table(['来源批次','源记录','版本','更新时间','更新原因'],rows)}`;
}
export async function render(id){if(!id)return `<h1>计算结果与解释</h1><p>选择一次运行查看冻结输入、算法结果与业务解释。</p>${runList()}`;const r=await runs.get(id);store.runId=r.id;const batch=r.batch_id?store.batches.find(b=>b.id===r.batch_id):null;return `<div class="eyebrow">03 / RESULTS ARCHIVE · RUN ${r.id}</div><h1>${esc(r.name)} ${status(r.status)} ${r.current?'<span class="status completed">当前有效</span>':r.status==='completed'?'<span class="status">历史版本</span>':''}</h1><div class="checksum">INPUT SHA256 ${r.checksum}${r.study_fingerprint?` · STUDY FINGERPRINT ${esc(r.study_fingerprint)}`:''}</div><p>快照 ${r.snapshot.length} 条 · ${esc(r.created_at)} · ${r.review?'负责人已复核':'尚未复核'}${r.aggregate?' · 联合研究去重汇总':''}</p>${batch?publicationNote(batch):''}${r.status==='completed'&&!r.current?`<div class="method-note">该结果已被更新版本取代，仅作为历史档案保留；当前生效版本请到结果档案查看。${esc(r.error||'')}</div>`:''}${r.status==='failed'?`<div class="error">计算失败：${esc(r.error)}${r.batch_id?'。同批结果未全部就绪，旧版结果继续生效。':'。请到来源页修复数据，然后创建新运行。'}</div>`:r.result?`<section class="result-intro"><strong>${esc(r.result.interpretation)}</strong><p>${esc(r.result.caveat)}</p></section><h2>核心数值</h2>${table(['业务指标','计算值'],Object.entries(r.result.metrics).map(([k,v])=>[k,fmt(v)]))}<h2>${esc(r.result.table_title||'计算明细')}</h2>${table(r.result.columns,r.result.rows)}${provenance(r)}<div class="toolbar"><button id="export" ${r.current?'':'disabled'}>下载核算 CSV</button><a class="button secondary" href="#review/${r.id}">打开业务复核 →</a></div>`:'<p class="method-note">任务正在队列中。此页每2秒更新，结果写入数据库后展示。</p>'}<details><summary>本次冻结的参数与源记录</summary><pre>${esc(JSON.stringify({parameters:r.parameters,snapshot:r.snapshot},null,2))}</pre></details>`;}
export function bind(){const button=document.getElementById('export');if(button)button.onclick=async()=>{try{download(await runs.export(store.runId),'research-'+store.runId+'.csv');}catch(e){document.getElementById('notice').textContent=e.message;}};}
