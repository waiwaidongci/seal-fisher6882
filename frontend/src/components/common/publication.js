import {PUBLICATION_LABELS} from '../../types/enums.js';
import {esc} from '../../utils/format.js';
export function publicationStatus(batch){
 if(!batch)return '<span class="status">尚无发布批次</span>';
 return `<span class="status ${batch.status==='applied'?'completed':batch.status==='failed'?'failed':''}">${esc(PUBLICATION_LABELS[batch.status]||batch.status)}</span>`;
}
export function publicationNote(batch){
 if(!batch)return '';
 const reason=batch.reason?`<p>${esc(batch.reason)}</p>`:'';
 return `<div class="method-note"><strong>发布批次 #${batch.id}</strong> ${publicationStatus(batch)}${reason}<small>${esc(batch.finished_at||batch.created_at||'')} · 触发：${esc(batch.trigger_kind||'手动')}</small></div>`;
}
