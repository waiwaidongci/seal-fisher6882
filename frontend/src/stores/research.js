import {studies,runs,publications} from '../api/research.js';
export const store={studies:[],runs:[],batches:[],studyId:0,runId:0,rows:[],dependencies:null};
export async function load(){[store.studies,store.runs,store.batches]=await Promise.all([studies.list(),runs.list(),publications.list()]);if(!store.studyId)store.studyId=store.studies[0]?.id||0;}
export async function loadRows(){store.rows=store.studyId?await studies.rows(store.studyId):[];}
export async function loadDependencies(){store.dependencies=store.studyId?await studies.dependencies(store.studyId):null;}
