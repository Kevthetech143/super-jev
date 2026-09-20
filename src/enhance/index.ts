/**
 * Context-enhancer primitives. Prototype: every module here runs offline
 * against any Evaluator, including the stub. None of it has been measured
 * against live traffic or real data.
 */
export { estimateTokens, budget, recordAllowance, DEFAULT_BUDGET } from './budget.ts';
export { planBatches, formatPlan } from './batch.ts';
export { assignIds, toRefs, buildKeyedRequest, buildArrayRequest, keyedPairs, arrayPairs, mapAnswers, mappingIsExact } from './reference.ts';
export type { AskedPair } from './reference.ts';
export { decideOutcome, readAnswer, actionAllowed, DEFAULT_GATE } from './outcome.ts';
export type { GateConfig, PassResult } from './outcome.ts';
export { buildManifest, assertComplete, formatManifest } from './coverage.ts';
export { CostMeter, formatCost } from './cost.ts';
export { traverse, checkCompleteness, gatherEvidence, parseProseReferences } from './evidence.ts';
export type { SourceDoc, EvidenceRole, EvidenceSpec, TraversalReport, RoleAssignment, CompletenessReport } from './evidence.ts';
export { StubEvaluator, scriptFromTable, choiceAnswer } from './stub.ts';
export { planEnhancedRun, runEnhancedClassification, DEFAULT_PASSES } from './classify.ts';
export type { ClassifyConfig, PassSpec, PassRecord, EnhancedRun } from './classify.ts';
export { planInvestigation, runInvestigation } from './investigate.ts';
export type { InvestigateConfig, InvestigateRun, InvestigationCase } from './investigate.ts';
export { planSweep, runSweep, formatSweepPlan, formatSweepReport, buildQuestion, buildSweepRequest, cellsFor,
  validateQuestions, validateCells, questionCostFor, foldRecordKind, MAX_QUESTIONS_PER_CALL, DEFAULT_SWEEP_GATE } from './sweep.ts';
export type { SweepQuestion, SweepInputRecord, SweepConfig, SweepCell, CellResult, RecordResult, SweepPlan, SweepRun } from './sweep.ts';
export type {
  EnhanceRecord, RecordRef, ContextBudget, PlannedCall, BatchPlan,
  OutcomeKind, RecordOutcome, CoverageManifest, CostAccount, MappingReport, StubScript
} from './types.ts';
export { PreparedCache, contentShaOf, artifactDigestOf, deriveCacheKey, entryDigest, makeReviewReceipt, PREPARED_CACHE_SCHEMA, CACHE_KEY_DOMAIN, CACHE_DIR_MODE, CACHE_FILE_MODE } from './prepared-cache.ts';
export type { PreparedArtifact, CacheBindings, ReviewReceipt, CacheMissReason, CacheLookup } from './prepared-cache.ts';
export {
  RetrievalError, DEFAULT_CHUNK_TARGET_WORDS, chunkIdentity, chunkSource, assertChunkOffsets,
  bm25EntryText, topChunksGlobal, preparePassages, bundleProviderText,
  cacheKeyFor, validateSelection, addNeighbors, retrieveSources, groupExactDuplicateSources
} from './retrieval.ts';
export type {
  RetrievalSource, SourceChunk, PreparationRecord, PreparationSource,
  PreparedPassage, UnpreparedPassage, PassageBundle, RetrievalCache, RetrievalOptions,
  RetrievalStatus, StageTrace, RetrievalResult, DuplicateSourceGroup, DuplicateGrouping
} from './retrieval.ts';
