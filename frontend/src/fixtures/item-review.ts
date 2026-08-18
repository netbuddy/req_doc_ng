import type { ItemReviewWorkspaceRead } from '../api/item-review';
import { itemFormationWorkspaceFixture } from './item-formation';
import { createPendingItemsFromElements } from '../view-models/requirement-item-formation';
import { buildInitialReviewWorkspace } from '../view-models/requirement-item-review';

const formedWorkspace = createPendingItemsFromElements(
  itemFormationWorkspaceFixture,
  itemFormationWorkspaceFixture.eligible_elements.map((element) => element.id),
);

export const itemReviewWorkspaceFixture: ItemReviewWorkspaceRead =
  buildInitialReviewWorkspace(formedWorkspace);
