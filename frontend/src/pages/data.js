import {DOMAIN} from '../constants/domain.js';
import {store,load,loadRows,loadDependencies} from '../stores/research.js';
import {studies} from '../api/research.js';
import {studySelect} from '../components/common/study-select.js';
import {publicationNote} from '../components/common/publication.js';
import {table} from '../components/common/table.js';
import {STUDY_KINDS} from '../types/enums.js';
import {esc} from '../utils/format.js';
import {bindForm} from '../hooks/forms.js';

function jointOptions(excludeId){
 return store.studies.filter(s=>s.id!==excludeId).map(s=>`<label class="check"><input type="checkbox" name="member" value="${s.id}" ${s.kind==='joint'?'data-joint="1"':''}> ${esc(s.name)} <small>${STUDY_KINDS[s.kind]||s.kind} · ${s.kind==='joint'?s.member_count+'引用':s.row_count+'条'}</small></label>`).join('');
}
function dependencyPanel(detail){
 if(!detail)return '';
 if(detail.error)return `<div class="error">依赖链无法解析：${esc(detail.error)}。上一版结果仍在使用。</div>`;
 const leaves=detail.leaves.map(leaf=>`<details open><summary>底层来源：${esc(leaf.name)} · ${leaf.row_count}条 · 最近更新 ${esc(leaf.latest_update||'—')}</summary>${table(['源记录','A/B','合格','失败','版本','更新原因'],leaf.rows.map(r=>[r.source_key,r.values.group,r.values.success,r.values.failure,'v'+r.revision,r.change_reason||'—']))}</details>`).join('');
 return `<p class="checksum">研究指纹 ${esc(detail.fingerprint)} · 按底层来源去重后参与比较</p>${leaves}`;
}
export async function render(){
 await loadRows();await loadDependencies();
 const writable=localStorage.getItem('research-role')!=='viewer';
 const current=store.studies.find(s=>s.id===store.studyId);
 const isJoint=current?.kind==='joint';
 return `<div class="eyebrow">01 / SOURCE REGISTER</div><h1>${DOMAIN.study_label}与${DOMAIN.row_label}</h1><p>${DOMAIN.note}</p><div class="toolbar">${studySelect()}</div>${current?publicationNote(current.current_batch):''}${current?.pending_batches?.length?`<div class="method-note"><strong>重算排队中</strong><p>批次 ${current.pending_batches.map(b=>'#'+b.id).join('、')} 正在等待关联结果全部就绪，当前展示的仍是上一版。${esc(current.pending_batches[0].reason||'')}</p></div>`:''}${writable?`<details><summary>新增${DOMAIN.study_label}</summary><form id="study-form" class="inline-form"><label>名称<input name="name" required minlength="2"></label><label>来源文件<input name="source_filename" value="field-notes-202611.csv" required></label><label>说明<input name="description"></label><button type="submit">保存批次</button></form></details><details><summary>组建联合研究（可引用多个批次或其他联合研究）</summary><form id="joint-form" class="inline-form"><label>联合研究名称<input name="name" required minlength="2"></label><label>说明<input name="description"></label><p class="hint">引用可多选；联合研究也能被再次引用。</p></form><div class="checklist" id="joint-members">${jointOptions(0)}</div><p><button type="button" id="joint-create">创建联合研究</button></p><p class="method-note">同一底层来源被多条引用路径重复带入时只计一次；取值冲突或循环引用会被拒绝。</p></details>`:''}${isJoint&&writable?`<details><summary>编辑当前联合研究的引用批次</summary><form id="members-form"><div class="checklist">${store.studies.filter(s=>s.id!==current.id).map(s=>`<label class="check"><input type="checkbox" name="member" value="${s.id}" ${(store.dependencies?.direct_members||[]).some(m=>m.id===s.id)?'checked':''}> ${esc(s.name)} <small>${STUDY_KINDS[s.kind]||s.kind}</small></label>`).join('')}</div><button type="submit">更新引用并重算受影响研究</button></form></details>`:''}${isJoint?`<h2>依赖与来源追踪 <small>更新后受影响的研究自动失效重算</small></h2>${dependencyPanel(store.dependencies)}`:''}${writable&&!isJoint?`<h2 id="row-heading">录入${DOMAIN.row_label}</h2><form id="row-form" class="inline-form"><label>源记录编号<input name="source_key" placeholder="如 FIELD-001" required></label>${DOMAIN.fields.map(([key,label,kind])=>`<label>${esc(label)}<input name="${key}" type="${kind}" ${kind==='number'?'step="any" min="0"':''} required></label>`).join('')}<label>更新原因<input name="change_reason" placeholder="如 复核发现抄录错误"></label><button type="submit">保存观测</button><button type="button" id="cancel-edit" hidden>取消编辑</button></form>`:''}<h2>观测明细 <small>${isJoint?'联合研究不直接录入，编辑其来源批次':store.rows.length+' 条 · 运行后更改不会改写历史快照'}</small></h2>${isJoint?'':table(['源记录',...DOMAIN.fields.map(f=>f[1]),'版本',...(writable?['操作']:[])],store.rows.map(row=>[row.source_key,...DOMAIN.fields.map(f=>row.values[f[0]]),'v'+(row.revision||1),...(writable?[{html:`<button class="text" data-edit="${row.id}">编辑</button> <button class="text" data-delete="${row.id}">移除</button>`}]:[])]))}`;
}
export function bind(refresh){
 let editing=null;
 const sel=document.getElementById('study-select');if(sel)sel.onchange=()=>{store.studyId=+sel.value;refresh();};
 bindForm('study-form',async data=>{const s=await studies.create(data);store.studyId=s.id;await load();refresh();});
 const create=document.getElementById('joint-create');
 if(create)create.onclick=async()=>{const form=document.getElementById('joint-form');const data=Object.fromEntries(new FormData(form));const member_ids=[...document.querySelectorAll('#joint-members input[name=member]:checked')].map(x=>+x.value);try{if(!member_ids.length)throw new Error('至少选择一个引用批次');const detail=await studies.createJoint({...data,member_ids});store.studyId=detail.study.id;await load();refresh();}catch(e){document.getElementById('notice').textContent=e.message;}};
 bindForm('members-form',async()=>{const member_ids=[...document.querySelectorAll('#members-form input[name=member]:checked')].map(x=>+x.value);try{await studies.members(store.studyId,{member_ids});await load();refresh();}catch(e){document.getElementById('notice').textContent=e.message;}});
 bindForm('row-form',async data=>{const source_key=data.source_key,reason=data.change_reason||'';delete data.source_key;delete data.change_reason;const values=Object.fromEntries(DOMAIN.fields.map(([key,label,kind])=>[key,kind==='number'?+data[key]:data[key]]));const saved=await studies.save(store.studyId,{source_key,values,change_reason:reason},editing);await load();refresh();const notice=document.getElementById('notice');if(notice&&saved&&saved.publication_batch)notice.textContent=`已保存；受影响研究已排队重算（批次 #${saved.publication_batch.id}），全部就绪后原子切换`;});
 document.querySelectorAll('[data-edit]').forEach(el=>el.onclick=()=>{const row=store.rows.find(r=>r.id===+el.dataset.edit),form=document.getElementById('row-form');editing=row.id;form.elements.source_key.value=row.source_key;form.elements.change_reason.value=row.change_reason||'';DOMAIN.fields.forEach(([key])=>form.elements[key].value=row.values[key]);document.getElementById('row-heading').textContent='编辑 '+row.source_key;document.getElementById('cancel-edit').hidden=false;form.scrollIntoView({behavior:'smooth',block:'center'});});
 const cancel=document.getElementById('cancel-edit');if(cancel)cancel.onclick=()=>refresh();
 document.querySelectorAll('[data-delete]').forEach(el=>el.onclick=async()=>{try{await studies.remove(store.studyId,+el.dataset.delete);await load();refresh();}catch(e){document.getElementById('notice').textContent=e.message;}});
}
