'use strict';

// Presentation only. Stored validation and rule-context references continue to
// control the backend review gate; a suggestion is never a sourced patient fact.
function clinicalPresentation(item, sources) {
 const types = {
  observed_signal: {status:'EVIDENCE_MATCHED',sourceLabel:'Evidence',explanation:'A demo expression matched this source. This is not a diagnosis.'},
  condition_candidate: {status:'RULE_DERIVED_CANDIDATE',sourceLabel:'Rule input',explanation:'A rule derived this candidate from the linked observations. The sources do not establish a condition.'},
  risk_signal: {status:'RULE_MATCH',sourceLabel:'Rule input',explanation:'A limited expression rule matched. This is not a risk level or clinical risk assessment.'},
  missing_information: {status:'NOT_OBSERVED',explanation:'The rule did not find this information in its input scope. This is not proof of absence and is not a transcript claim.'},
  follow_up_question: {status:'SUGGESTED',explanation:'A question proposed for clinician review, not a statement reported in the transcript.'},
  next_assessment: {status:'RECOMMENDED_FOR_REVIEW',explanation:'An assessment suggestion for review, not an assessment performed or a treatment instruction.'},
  recommendation: {status:'REVIEW_CANDIDATE',explanation:'A review proposal, not a transcript fact or an established clinical recommendation.'}
 };
 const type = types[item.kind] || {status:'REQUIRES_REVIEW',explanation:'Unknown item type; clinician review required.'};
 const refs = [...new Set(item.evidence || [])];
 const evidence = type.sourceLabel ? refs.filter(seq=>sources.some(u=>u.sequence===seq)) : [];
 let warning = item.current_validation === 'SUPPORTED' ? null : (item.current_validation || 'UNSUPPORTED');
 if(type.sourceLabel && (!refs.length || evidence.length!==refs.length))warning=warning||'UNSUPPORTED';
 return {
  ...type,
  status:item.kind==='observed_signal' && warning ? 'NOT_MATCHED' : type.status,
  evidence,
  warning,
  requiresReview:['condition_candidate','risk_signal'].includes(item.kind),
  validationText:warning ? `Rule/source check needs attention: ${warning}` : 'Rule/source check passed; this does not establish clinical validity.'
 };
}
