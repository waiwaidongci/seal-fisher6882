import {store,load} from '../stores/research.js';
import {table} from '../components/common/table.js';
import {publicationStatus} from '../components/common/publication.js';
import {esc} from '../utils/format.js';
export async function render(){return `<div class="eyebrow">05 / PUBLICATION LEDGER</div><h1>发布批次与失效重算</h1><p>源数据更新后，受影响依赖链自动重算；同一批次的所有关联结果全部就绪才整体切换。等待、失败或过期时旧版继续生效，并在这里写明原因。未受影响的研究不会重算。</p>${table(['批次','状态','触发','关联研究 / 运行','原因与时间'],store.batches.map(b=>[`#${b.id}`,{html:publicationStatus(b)},esc(b.trigger_kind||'—'),b.items.map(i=>`${esc(i.study_name)} · 运行#${i.run_id} (${esc(i.run_status)})`).join('<br>'),`${esc(b.reason||'—')}<br><small>${esc(b.finished_at||b.created_at)}</small>`]))}`;}
export function bind(){}
