/**
 * Case deletion, bound to the real endpoint.
 *
 * A thin adapter: every rule lives in `lib/casedelete`, and this hook only holds
 * the state in React and supplies `api.deleteCase` as the transport. Both the
 * case queue and the case dossier use it, so the two screens cannot drift apart
 * on what a confirmed deletion means.
 *
 * There is deliberately no abort: once DELETE has left, the backend may already
 * have committed the transaction and appended the CASE_DELETED audit row.
 * Cancelling the request at that point would only hide the outcome from the
 * operator, so the dialog stays open and busy until the backend answers.
 */

import { useCallback, useRef, useState } from 'react'
import { api } from '../api'
import type { CaseDeleteResult, CaseRecord } from '../api/types'
import {
  IDLE_DELETION,
  askToDelete,
  dismissDeletion,
  runCaseDeletion,
  typeConfirmation,
  type CaseDeletionState,
} from '../lib/casedelete'

export interface CaseDeletion {
  state: CaseDeletionState
  /** Open the confirmation dialog for a case that is really on screen. */
  ask: (target: CaseRecord) => void
  /** Record what the operator typed into the confirmation field. */
  type: (value: string) => void
  /** Close the dialog. Ignored while the request is in flight. */
  dismiss: () => void
  /** Send the DELETE. Does nothing until the case number has been typed back. */
  confirm: () => Promise<void>
}

export function useCaseDeletion(
  onDeleted?: (result: CaseDeleteResult, target: CaseRecord) => void,
): CaseDeletion {
  const [state, setState] = useState<CaseDeletionState>(IDLE_DELETION)

  // `confirm` is called from a click handler and must act on the state as it is
  // at that moment, not on whatever was current when the callback was built.
  const latest = useRef(state)
  const publish = useCallback((next: CaseDeletionState) => {
    latest.current = next
    setState(next)
  }, [])

  const ask = useCallback(
    (target: CaseRecord) => publish(askToDelete(target)),
    [publish],
  )
  const type = useCallback(
    (value: string) => publish(typeConfirmation(latest.current, value)),
    [publish],
  )
  const dismiss = useCallback(
    () => publish(dismissDeletion(latest.current)),
    [publish],
  )

  const confirm = useCallback(async () => {
    await runCaseDeletion(latest.current, {
      deleteCase: (caseId) => api.deleteCase(caseId),
      emit: publish,
      onDeleted,
    })
  }, [onDeleted, publish])

  return { state, ask, type, dismiss, confirm }
}
