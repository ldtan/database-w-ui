# Graph Report - database-w-ui  (2026-08-28)

## Corpus Check
- Large corpus: 449 files · ~739,666 words. Semantic extraction will be expensive (many Claude tokens). Consider running on a subfolder.

## Summary
- 4699 nodes · 11817 edges · 244 communities (217 shown, 27 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 1607 edges (avg confidence: 0.88)
- Token cost: 526,477 input · 0 output

## Community Hubs (Navigation)
- Column Type Conversion
- Shared UI Primitives
- App Shell & Navigation
- Inline Cell Edit Rules
- Conversion Regression Tests
- List HTTP Flow Tests
- Principal (User/Group) Controls
- List Detail Page
- Permission Grants Model
- Create Schemas & Grant Upsert
- List Dependencies & Metrics
- Column Advanced Config & Sharing
- Column Routes & Errors
- Form Field Settings
- Lookup Options & Label Search
- Purge Maintenance Jobs
- Demo Scenario Builders
- List Service
- User & Group Administration
- Forms & Bin Routes
- Import/Export Transfer Tests
- Group Membership Cache Tests
- Column Dialog & Page Chrome
- Temporal Input & Formatting
- Core & Column Schemas
- Calculated Column Chain
- Purge Record Draining
- Metric Formula Tests
- User & Group Routes
- List Clone Tests
- Views & Bin Schemas
- Nested Lookup Tests
- Reference Column Sorting
- Orphan Grant Sweep
- Lookup Grant Tests
- User Batch Import Tests
- Ad-hoc Query Filtering
- Lookup Option Filters
- Phone & Email Inputs
- School Demo Seeder
- Workspace Service & Routes
- Principal Bin Tests
- Rich Text Column Tests
- Auth Token Endpoints
- Backfill & Recompute Jobs
- List Config Bin Tests
- People & Sharing Screens
- List Item Service Reads
- Batch User & Import Dialogs
- Auth Dependencies & Guards
- Workspace Service Internals
- Formula Evaluation Tests
- Record Archive Tests
- Formula Language Tests
- Metrics Tests
- List Routes & Conflicts
- List Item Service Writes
- Metric Aggregate Language
- Item Routes & Schemas
- Import/Export Guide Screens
- Batch User Parsing Service
- Search & Recompute Locking
- User Settings Tests
- Metric Pushdown Tests
- Metric SQL Pushdown
- People Service & Routes
- Add Column/Record Screens
- Dangling Lookup Target Sweep
- Choice 'Other' Option Tests
- Form Condition Tests
- Live Event Stream Tests
- Sign-in & Toolbar Screens
- Lookup Controls & Display Column
- TypeScript Config
- Batch Parsing Tests
- Formula Parsing & Checks
- Account Security Tests
- Column Settings Screens
- Guided Tour & Help
- Live Stream Client
- Record Paging Tests
- List Transfer Tests
- Login Throttle Tests
- Workspace Transfer Tests
- Frontend Runtime Dependencies
- Frontend Dev Dependencies
- Location Field & Geocoding
- Login Rate Limiting
- Bin Maintenance Helpers
- Bin Service
- Formula Runtime Functions
- Location Column Tests
- Name Slugification
- Geocode Proxy
- View Filtering Tests
- Multiple Choice Tests
- List Config Purge Tests
- Theme & Figures
- Conversion Service Internals
- Duration Column Tests
- Phone Column Tests
- Person Model & Routes
- User Settings Schemas
- Search & UUID Utilities
- Item Update & Row Lock
- Schema Drift Check
- Conversion Scratch Cleanup
- Migration Tests
- Token Revocation Tests
- App Factory & Health
- Backend README & Demos
- Formula & Naming Docs
- Unique + Multiple Guard Tests
- Guide Screenshot Capture
- CSV Upload Route
- Calculated Column Tests
- Geocode Tests
- Rating Column Tests
- Timestamp Mixins
- People CRUD Tests
- Lifecycle & Sharing Docs
- Test Fixtures & Engine
- Calculated Reference Tests
- Cell Patch Tests
- Text Mode Tests
- Database Session Setup
- Color Column Tests
- Email Column Tests
- Link Column Tests
- Render Deployment Blueprint
- Group Slug Migration
- Deployment Config Docs
- Live Event Fixtures
- App Settings
- School Principal Seeding
- Record Purge Tests
- Maintenance Workflow Docs
- Batch Accounts & Tenancy Docs
- Column Type Docs
- Stream Subscribe & Shutdown
- List Event Broker
- List Config Purge Job
- Calculated Column Fixtures
- Filter Token Resolution
- Soft Delete Reversibility
- Frontend NPM Scripts
- Nav Tree Component
- Alembic Environment
- Demo Sharing Grants
- List Purge Job
- Workspace Purge Job
- Workspace Dependencies
- List Purge Tests
- Workspace Purge Tests
- Event Stream Route
- Visible Record Query
- Error Boundary
- Demo World Helper
- Workspace List Bin
- List Bin Tests
- Workspace Bin Tests
- CSV Header Validation
- Batch User Row Schema
- Calculated Index Corruption
- Formula Clock Fixtures
- Archive Test Fixtures
- Metric Pushdown Fixtures
- Permanent Delete Tests
- Session Config Tests
- Unique Concurrency Tests
- Plan & Spec Documents
- Editability Predicate Findings
- Frontend Package Identity
- Deployment Invariants
- View Form Backfill Migration
- Long Text Merge Migration
- Archived Record Queries
- Aggregate Accumulator
- Label Cache Loader
- Formula Rejection Tests
- Pydantic Error Flattening
- Conversion Test Client
- Metric Test Fixtures
- Per-row Transaction Handling
- Frontend Layout Conventions
- Vite Environment Types
- Demo Package
- Model Registry
- Live Events Package
- Recompute Idempotence Test
- Formula Edit Sorting Test
- Partial Reindex Test
- Calculated Filter Test
- Calculated Text Search Test
- Calculated Group-By Test
- Choice Reference Test
- Clock Formula Test
- Calculated Cast Index Test
- Hookform Resolvers Dependency
- Leaflet Dependency
- Quill Dependency
- React Hook Form Dependency
- React Query Dependency
- Zod Dependency
- Project Metadata

## God Nodes (most connected - your core abstractions)
1. `session_factory()` - 239 edges
2. `add_column()` - 220 edges
3. `db()` - 176 edges
4. `User` - 149 edges
5. `BaseModel` - 89 edges
6. `ConflictError` - 72 edges
7. `ValidationError` - 72 edges
8. `Column` - 72 edges
9. `ListItemService` - 70 edges
10. `useToast()` - 59 edges

## Surprising Connections (you probably didn't know these)
- `parse_file()` --semantically_similar_to--> `Pure-logic vitest harness (node env, no jsdom)`  [INFERRED] [semantically similar]
  backend/src/auth/users/batch/parsing.py → docs/superpowers/specs/2026-07-31-inline-cell-editing-findings-design.md
- `Splitting cellEdit.ts out of inlineEdit.tsx so rules are testable without a DOM` --semantically_similar_to--> `parse_file()`  [INFERRED] [semantically similar]
  docs/superpowers/plans/2026-07-31-inline-cell-editing-findings.md → backend/src/auth/users/batch/parsing.py
- `_undo_row()` --semantically_similar_to--> `saveCell mutation`  [INFERRED] [semantically similar]
  backend/src/auth/users/batch/services.py → docs/superpowers/plans/2026-07-31-inline-cell-editing-findings.md
- `Slug on the wire, display name in the app` --semantically_similar_to--> `toStored()`  [INFERRED] [semantically similar]
  docs/superpowers/specs/2026-07-31-group-name-display-name-split-design.md → frontend/src/features/records/recordValues.ts
- `Skip-and-report per-row error handling` --rationale_for--> `BatchUserError`  [EXTRACTED]
  docs/superpowers/specs/2026-07-31-user-batch-creation-design.md → backend/src/auth/users/batch/schemas.py

## Import Cycles
- 4-file cycle: `frontend/src/features/lookups/CreateTargetRowDialog.tsx -> frontend/src/features/records/RecordFormDialog.tsx -> frontend/src/features/records/RecordFields.tsx -> frontend/src/features/lookups/LookupControls.tsx -> frontend/src/features/lookups/CreateTargetRowDialog.tsx`

## Hyperedges (group relationships)
- **The ordered nightly maintenance rotation** — _github_workflows_maintenance_daily_maintenance, backend_readme_reconcile_shapes, backend_readme_purge_deleted_workspaces, backend_readme_purge_deleted_lists, backend_readme_purge_deleted_items, backend_readme_purge_deleted_list_config, backend_readme_purge_deleted_principals, backend_readme_sweep_orphan_grants, backend_readme_recompute_calculated, _github_workflows_maintenance_job_ordering [EXTRACTED 1.00]
- **The Render deployment: three services, one env group, one origin** — deploy_render_render_blueprint, deploy_render_render_dbui_db, deploy_render_render_dbui_api, deploy_render_render_dbui_web, deploy_render_render_dbui_env_group, deploy_render_render_same_origin_rewrite, deploy_render_render_predeploy_migration, deploy_render_render_single_instance_assumption [EXTRACTED 1.00]
- **Live records: server push with a polling fallback** — backend_readme_live_records_sse, backend_readme_sse_revocation_window, deploy_render_render_sse_kill_switch, deploy_render_render_vite_live_refresh_seconds, deploy_render_render_refresh_floors, frontend_readme_use_live_list [EXTRACTED 1.00]
- **Inline cell write path: draft → patch → optimistic write → rollback → debounced settle** — frontend_src_features_records_celledit_celldraft, frontend_src_features_lists_listdetailpage_commitcell, frontend_src_features_records_celledit_cellpatch, frontend_src_features_lists_listdetailpage_savecell, frontend_src_features_lists_listdetailpage_withitem, frontend_src_features_lists_listdetailpage_schedulesettle [EXTRACTED 1.00]
- **One text-mode vocabulary read by every surface that branches on it** — frontend_src_features_records_recordvalues_textmode, frontend_src_features_records_recordvalues_needsfulleditor, frontend_src_features_records_celledit_celleditkinds, frontend_src_features_records_rendercell_rendercell, frontend_src_features_records_recordfields_recordfield [EXTRACTED 1.00]
- **Batch user row pipeline: parse pure, then create through the existing account services** — backend_src_auth_users_batch_parsing_parse_file, backend_src_auth_users_batch_schemas_batchuserrow, backend_src_auth_users_batch_services_resolve_groups, backend_src_auth_users_batch_services_import_users, backend_src_auth_users_services_userservice, backend_src_people_services_personservice, backend_src_auth_users_batch_services_undo_row [EXTRACTED 1.00]
- **Guide modal dialog family (title bar, sectioned body, Cancel + primary action footer)** — frontend_public_guide_add_column_dark_add_column_dialog, frontend_public_guide_add_record_dark_add_record_dialog, frontend_public_guide_bin_dark_deleted_records_dialog, frontend_public_guide_change_type_dark_convert_column_dialog [INFERRED 0.85]
- **Column lifecycle flow: define type and options, then convert with a previewed dry run** — frontend_public_guide_add_column_dark_type_selector, frontend_public_guide_add_column_dark_text_input_mode_toggle, frontend_public_guide_change_type_dark_hidden_column_dry_run, frontend_public_guide_change_type_dark_two_step_conversion_via_text, frontend_public_guide_change_type_dark_filling_in_section [INFERRED 0.85]
- **Reversibility promises made to the user across the guide (nothing destructive happens without a preview or an undo)** — frontend_public_guide_change_type_dark_hidden_column_dry_run, frontend_public_guide_bin_dark_retention_window, frontend_public_guide_add_column_dark_text_input_mode_toggle [INFERRED 0.75]
- **Shared modal design language across guide screens (titled header with close X, body, right-aligned footer with cancel + indigo primary action)** — frontend_public_guide_columns_dark_screen, frontend_public_guide_duplicate_dark_screen, frontend_public_guide_filter_dark_screen, frontend_public_guide_forms_dark_screen, frontend_public_guide_formula_dark_screen [INFERRED 0.95]
- **List configuration surface: the settings tabs, column definitions and form field config all edit one list's schema** — frontend_public_guide_columns_dark_settings_tab_bar, frontend_public_guide_columns_dark_column_type_catalog, frontend_public_guide_columns_dark_contacts_demo_schema, frontend_public_guide_forms_dark_field_config_matrix, frontend_public_guide_formula_dark_calculated_column [INFERRED 0.85]
- **Columns are referenced by API name across formulas, filters and forms, which is why renaming one breaks dependents** — frontend_public_guide_formula_dark_api_name, frontend_public_guide_formula_dark_function_library, frontend_public_guide_filter_dark_condition_builder, frontend_public_guide_forms_dark_field_config_matrix [INFERRED 0.85]
- **Guide screenshot suite shipped as light/dark pairs** — frontend_public_guide_import_export_dark_import_export_dialog, frontend_public_guide_lists_dark_lists_screen, frontend_public_guide_metrics_dark_metrics_bar, frontend_public_guide_nav_tree_dark_nav_tree, frontend_public_guide_nested_lookup_dark_add_record_dialog, frontend_public_guide_theming_light_dark_theme_parity [EXTRACTED 1.00]
- **View state (filter/sort/columns) drives grid, metrics, and export scope** — frontend_public_guide_metrics_dark_view_toolbar_controls, frontend_public_guide_metrics_dark_saved_view, frontend_public_guide_metrics_dark_metric_tiles, frontend_public_guide_import_export_dark_which_records_scope, frontend_public_guide_import_export_dark_column_selection [INFERRED 0.85]
- **Workspace to list to record navigation spine** — frontend_public_guide_nav_tree_dark_workspace_list_hierarchy, frontend_public_guide_lists_dark_breadcrumb_trail, frontend_public_guide_lists_dark_list_card, frontend_public_guide_nested_lookup_dark_add_record_dialog, frontend_public_guide_metrics_dark_metrics_bar [INFERRED 0.85]
- **Guide Screenshot Set Documenting the Product Walkthrough** — frontend_public_guide_shell_dark_app_shell, frontend_public_guide_records_dark_grid_view, frontend_public_guide_people_dark_people_and_groups, frontend_public_guide_sharing_dark_list_settings_sharing, frontend_public_guide_qr_menu_dark_add_record_menu, frontend_public_guide_shell_dark_guide_screen_set [INFERRED 0.95]
- **Access Control Model Shown Across Guide Screens** — frontend_public_guide_people_dark_people_and_groups, frontend_public_guide_people_dark_directory_vs_access, frontend_public_guide_sharing_dark_role_presets, frontend_public_guide_sharing_dark_permission_chips, frontend_public_guide_sharing_dark_access_expiry, frontend_public_guide_sharing_dark_inheritance_override, frontend_public_guide_shell_dark_shared_with_me [INFERRED 0.85]
- **View Shaping Controls Over a Record List** — frontend_public_guide_records_dark_view_toolbar, frontend_public_guide_records_dark_saved_views, frontend_public_guide_records_dark_column_sort_headers, frontend_public_guide_records_dark_pagination, frontend_public_guide_records_dark_metrics_panel [EXTRACTED 1.00]
- **Product Guide Screenshot Walkthrough (sign-in to workspaces to lists)** — frontend_public_guide_sign_in_dark_sign_in_screen, frontend_public_guide_workspaces_dark_workspaces_page, frontend_public_guide_toolbar_dark_list_toolbar, frontend_public_guide_views_dark_view_selector, frontend_public_guide_transfer_dark_move_or_transfer_dialog [INFERRED 0.85]
- **List Shaping and View Persistence Flow** — frontend_public_guide_toolbar_dark_shaping_controls, frontend_public_guide_toolbar_dark_save_view_action, frontend_public_guide_views_dark_saved_view, frontend_public_guide_views_dark_sortable_column_headers, frontend_public_guide_toolbar_dark_record_search [INFERRED 0.85]
- **Ownership and Access Model (workspace containment, sharing, move, transfer)** — frontend_public_guide_workspaces_dark_workspace, frontend_public_guide_workspaces_dark_shared_with_me, frontend_public_guide_workspaces_dark_workspace_share_action, frontend_public_guide_transfer_dark_move_to_workspace, frontend_public_guide_transfer_dark_transfer_to_user [INFERRED 0.85]

## Communities (244 total, 27 thin omitted)

### Community 0 - "Column Type Conversion"
Cohesion: 0.03
Nodes (130): Turn one column's stored values into another column's type. The engine behind…, One already-resolved value as text. Matches the export's `_csv_cell` except for…, _render_scalar(), Run a column type conversion: preview it, then apply it in one transaction. Two…, ColumnType, str, AsyncSession, ColumnType (+122 more)

### Community 1 - "Shared UI Primitives"
Cohesion: 0.06
Nodes (69): AppDialog(), BREAKPOINTS, CreateNameDialog(), Props, EmptyState(), Props, CheckboxField(), Field() (+61 more)

### Community 2 - "App Shell & Navigation"
Cohesion: 0.05
Nodes (66): App(), RequireAuth(), AccountMenu(), Props, AppLayout(), AppSidebar(), Props, initials() (+58 more)

### Community 3 - "Inline Cell Edit Rules"
Cohesion: 0.05
Nodes (72): Splitting cellEdit.ts out of inlineEdit.tsx so rules are testable without a DOM, Cite PrimeReact file:line for any behavioural claim, Slug on the wire, display name in the app, data-pr-is-overlay as PrimeReact's own overlay contract, Finding #10 — Escape is overloaded between overlay and cell, Finding #11 — rich-text and multiline cells are keyboard-unreachable, Finding #13 — two duplicated rules, one home each, applyFieldConditions() (+64 more)

### Community 4 - "Conversion Regression Tests"
Cohesion: 0.06
Nodes (89): _add_rows(), _apply(), _convert(), _hidden_columns(), parametrize, Changing a column's type after it holds records. The design in one line:…, The exact shape of the old data loss. The record is created after the report,…, Raw JSONB for every record, bypassing the API's shaping. (+81 more)

### Community 5 - "List HTTP Flow Tests"
Cohesion: 0.03
Nodes (68): add_column(), Return a helper that creates a column and returns its uuid., _child_counts(), paged_list(), fixture, End-to-end tests over the HTTP API: workspaces, lists, columns, records., Ten records: `n` runs "00".."09" in creation order, `grp` alternates., The correctness half of keyset paging, and why it is not only the faster one.… (+60 more)

### Community 6 - "Principal (User/Group) Controls"
Cohesion: 0.06
Nodes (58): Finding #5 — a link cell advertises an editor and navigates instead, labelOf(), nameFor(), Option, PATHS, PrincipalCell(), PrincipalField(), PrincipalFilterValue() (+50 more)

### Community 7 - "List Detail Page"
Cohesion: 0.05
Nodes (49): UserGroupService._apply_search, ListItemService._reference_matches, Search matches name OR display_name, Finding #7 — rollback, the returned record, one refetch per burst, ListDetailPage(), commitCell(), discardNewRow(), groupHeader() (+41 more)

### Community 8 - "Permission Grants Model"
Cohesion: 0.08
Nodes (45): Work out, once, how each reference column resolves — and whether it may., Action, expand_actions(), str, What a principal may do on a resource. ``view/create/edit/delete`` act on…, Kinds of resource a permission grant can target. ``item``/``view``/``form``…, Expand a collection of granted actions (Action or str) into the full set they…, ResourceType (+37 more)

### Community 9 - "Create Schemas & Grant Upsert"
Cohesion: 0.08
Nodes (61): ListItemCreate, ListCreate, GrantCreate, model_validator, Grant (or overwrite) a principal's actions on a resource., test_the_stream_is_gated_on_being_able_to_view_the_list(), make_client(), The metric is one definition, but its value is a permissioned read — so a… (+53 more)

### Community 10 - "List Dependencies & Metrics"
Cohesion: 0.08
Nodes (50): filter_param(), get_list_or_404(), AsyncSession, UUID, Resolve a live list by uuid without a permission check. Item routes depend on…, Resolve a list the current user can view, or raise 404/403. List-level routers…, A per-request filter, parsed from the query string. Filtering used to be…, resolve_list_or_404() (+42 more)

### Community 11 - "Column Advanced Config & Sharing"
Cohesion: 0.05
Nodes (53): BarProps, SkeletonRows(), ADVANCED_CONFIG_KEYS, ADVANCED_KEYS, hasAdvancedConfig(), isSet(), collectLookupUuids(), Props (+45 more)

### Community 12 - "Column Routes & Errors"
Cohesion: 0.10
Nodes (46): A referenced resource does not exist., Submitted data failed domain validation (bad column config, record value, etc.)., UnknownReferenceError, ValidationError, ConversionService, apply_conversion(), _column_or_404(), create_column() (+38 more)

### Community 13 - "Form Field Settings"
Cohesion: 0.07
Nodes (41): DRAG_GRIP_WIDTH, SortableList(), SortableListProps, EFFECTS, FieldSettingsPanel(), Props, buildFormConfigPatch(), FormFieldRow (+33 more)

### Community 14 - "Lookup Options & Label Search"
Cohesion: 0.08
Nodes (54): UserCreate, user_id(), `permission_grants` has no foreign key to its resource, so the hard delete is…, test_purging_a_view_takes_its_grants(), A reference cell stores a uuid, but what a person types is the label. Search…, test_search_matches_lookup_and_user_labels(), _user(), make_client() (+46 more)

### Community 15 - "Purge Maintenance Jobs"
Cohesion: 0.06
Nodes (46): _count(), _main(), purge(), AsyncSession, datetime, Destroy records that have been in the bin longer than the retention window.…, Destroy soft-deleted records older than ``days``. Returns how many went., _main() (+38 more)

### Community 16 - "Demo Scenario Builders"
Cohesion: 0.09
Nodes (44): bin_records(), ensure_column(), ensure_form(), ensure_group(), ensure_list(), ensure_metric(), ensure_person(), ensure_user() (+36 more)

### Community 17 - "List Service"
Cohesion: 0.10
Nodes (24): ListService, AsyncSession, ColumnElement, datetime, UUID, Bring a soft-deleted list back, along with the columns, records, views, forms…, Un-delete the lists (and their descendants) a workspace took down when it was…, Permanently destroy a single soft-deleted list and everything under it. Returns… (+16 more)

### Community 18 - "User & Group Administration"
Cohesion: 0.11
Nodes (24): UserGroup, update_group(), UserGroupUpdate, _coerce_uuid(), _guard_system_group, AsyncSession, UserSettingsUpdate, UUID (+16 more)

### Community 19 - "Forms & Bin Routes"
Cohesion: 0.12
Nodes (32): FormType, str, Form, create_form(), delete_form(), get_form(), list_deleted_forms(), list_forms() (+24 more)

### Community 20 - "Import/Export Transfer Tests"
Cohesion: 0.07
Nodes (44): Streaming export and bulk import of records., A target list labelled by its primary column, and a lookup column at it.…, An export is a snapshot of a view, so it has to be ordered like the view. The…, A row uuid in a CSV means nothing to a human, so a lookup exports the row's…, Row labels are not unique, so a label matching two rows has no right answer: it…, The error carries the row as the file wrote it, so the caller can put the…, A JSON upload's typed cells are stringified the way export writes them, so the…, There are no headers to hand back for a bare scalar, and the message says so.… (+36 more)

### Community 21 - "Group Membership Cache Tests"
Cohesion: 0.07
Nodes (40): UserGroupCreate, principals(), fixture, _make_user(), A session is not always one caller's — the batch importer and the maintenance…, The cache sits in front of an authorization answer, so it has to be dropped by…, Deleting a group keeps its membership rows — that is what makes the restore a…, test_deleting_a_group_drops_the_cache() (+32 more)

### Community 22 - "Column Dialog & Page Chrome"
Cohesion: 0.07
Nodes (27): Breadcrumbs(), Crumb, PageHeader(), Props, ColumnDialog(), dynamicLabel(), dynamicToken(), phoneFormatOptions() (+19 more)

### Community 23 - "Temporal Input & Formatting"
Cohesion: 0.12
Nodes (40): PICKER_PT, TemporalInput(), commit(), enter(), pick(), TemporalInputMode, TemporalFilterValue(), calendarDate() (+32 more)

### Community 24 - "Core & Column Schemas"
Cohesion: 0.07
Nodes (37): BaseModel, declared_attr, Mapped, ResourceLink, ColumnConfig, ColumnRead, ConversionFailure, ConversionReport (+29 more)

### Community 25 - "Calculated Column Chain"
Cohesion: 0.07
Nodes (35): apply_formulas(), build_chain(), find_cycle(), formula_edges(), parse_formulas(), ChainStep, Column, The order a list's `calculated` columns are evaluated in, and the loops to… (+27 more)

### Community 26 - "Purge Record Draining"
Cohesion: 0.09
Nodes (41): drain_records(), AsyncSession, Remove ``keys`` from every record of one list. Returns how many were rewritten.…, Destroy every record of ``list_ids`` in batches. Returns how many went. Takes…, strip_column_keys(), _age_everything(), _count(), filled_list() (+33 more)

### Community 27 - "Metric Formula Tests"
Cohesion: 0.07
Nodes (40): parametrize, The metric aggregate language, unit-tested without a database. A metric formula…, One argument is "the smallest of any record"; several is the row function…, A total over a text column is not an error, it simply has nothing numeric to…, One unanswerable record must not fail the whole metric., Inside an aggregate it asks about one record's cell; outside it asks about the…, A metric over records none of which count divides by zero, and IS_NULL is the…, It came with the row language and reaches the aggregate one for free. Outside… (+32 more)

### Community 28 - "User & Group Routes"
Cohesion: 0.10
Nodes (38): create_group(), create_user(), delete_group(), delete_user(), get_group(), get_me(), get_user(), list_deleted_groups() (+30 more)

### Community 29 - "List Clone Tests"
Cohesion: 0.15
Nodes (40): client(), _add_column(), _column_named(), make_client(), _make_user(), fixture, Cloning a list. A clone's columns are new rows with new uuids, so the load-…, A list's records straight from the table, in id order. The API is the wrong… (+32 more)

### Community 30 - "Views & Bin Schemas"
Cohesion: 0.13
Nodes (29): BinnedChildRead, One soft-deleted view, form or metric. Narrower than the live read schemas on…, View, create_view(), delete_view(), get_view(), list_deleted_views(), list_views() (+21 more)

### Community 31 - "Nested Lookup Tests"
Cohesion: 0.09
Nodes (39): _add_row(), _city_config(), _labels(), _named_list(), Nested lookup columns: what you pick in lookup A constrains lookup B. A lookup…, A list labelled by a primary `name` column; returns (list_uuid, name_col)., Two lists pointing at each other allow A-under-B *or* B-under-A — not both., Config PATCHes merge; explicit nulls are how a pairing is removed. (+31 more)

### Community 32 - "Reference Column Sorting"
Cohesion: 0.12
Nodes (38): _cells(), _host(), _labels(), make_client(), fixture, Ordering a listing by a reference column — by the label, not the uuid. A…, The rendered label of each row's cell, in the order the page came back., The whole feature. Seeded so uuid order is the reverse of label order, which is… (+30 more)

### Community 33 - "Orphan Grant Sweep"
Cohesion: 0.09
Nodes (35): _main(), _predicate(), Delete permission grants that can never grant anything again. uv run python -m…, Delete grants whose resource/principal is gone or whose expiry has passed., sweep(), _backdate(), _cells(), _expire_target() (+27 more)

### Community 34 - "Lookup Grant Tests"
Cohesion: 0.12
Nodes (37): _grant(), make_client(), fixture, The `lookup` permission action: sharing a list *as a lookup target only*.…, `name` gets `view` on the host list and `lookup` on its target — the shape the…, The whole point, in one test: the picker works, and the list it reads is…, `view` confers `lookup`, so a grant written before this action existed keeps…, A target you cannot name is a target you cannot point a column at. The picker's… (+29 more)

### Community 35 - "User Batch Import Tests"
Cohesion: 0.12
Nodes (34): verify_password(), make_client(), fixture, parametrize, Creating many accounts from one file. Runs against the test database because…, The file names groups by slug. Accepting the label too would hand back the…, The likeliest bad file is one written before the split, so the error earns its…, The summary promises a reported row created nothing. Step 1 of the pipeline… (+26 more)

### Community 36 - "Ad-hoc Query Filtering"
Cohesion: 0.10
Nodes (35): _cond(), _filtered_view(), _filters(), Filtering and grouping a list for one request, without saving a view. Filtering…, How a caller asks a filtered view for everything. Only expressible because…, The other half of that distinction, and the compatibility guarantee: a caller…, `q` is not an override. It is the one thing here that composes., A filter group as the query parameter carries it. (+27 more)

### Community 37 - "Lookup Option Filters"
Cohesion: 0.13
Nodes (35): _active(), _grant(), _labels(), _lookup_at(), make_client(), fixture, A lookup column's saved option filter: which target rows the picker offers. A…, `q` and the filter compose: both are `where` clauses on one statement, so a… (+27 more)

### Community 38 - "Phone & Email Inputs"
Cohesion: 0.15
Nodes (29): PhoneInput(), commit(), enter(), PhoneInputMode, isEmail(), mailtoHref(), normalizeEmail(), allowedRegions() (+21 more)

### Community 39 - "School Demo Seeder"
Cohesion: 0.13
Nodes (34): ensure_view(), ensure_workspace(), View, A workspace. `name` is unique per owner, so that is what identifies it., A saved view. A binned one counts as already there — see `ensure_column`., _academics_views_and_metrics(), build(), build_academics() (+26 more)

### Community 40 - "Workspace Service & Routes"
Cohesion: 0.14
Nodes (27): create_workspace(), get_workspace(), list_deleted_workspaces(), list_workspaces(), purge_workspace(), AsyncSession, get, patch (+19 more)

### Community 41 - "Principal Bin Tests"
Cohesion: 0.07
Nodes (32): alice(), _disown(), editors(), _expire(), make_client(), fixture, The bin for deleted accounts and groups. The last surface where a delete was…, Push a deletion past the retention window, where the bin stops offering it. (+24 more)

### Community 42 - "Rich Text Column Tests"
Cohesion: 0.12
Nodes (33): _add_rich_column(), parametrize, Rich text cells, over the HTTP API. A `text` column in `rich` mode stores HTML…, Not just create: the same choke point covers PATCH., The other write path into a list, and the one an editor never touches., A limit is about how much someone may write, not how much they formatted., <p>ab</p><p>cd</p>" is four characters of text and one word boundary., Only `rich` is HTML. A single-line column stores exactly what it was sent,… (+25 more)

### Community 43 - "Auth Token Endpoints"
Cohesion: 0.13
Nodes (24): get_public_key(), login_for_access_token(), AsyncSession, get, post, Request, Expose the public key for other microservices to verify tokens., End a session server-side. What signing out has to call. Clearing the tokens in… (+16 more)

### Community 44 - "Backfill & Recompute Jobs"
Cohesion: 0.10
Nodes (30): count_records(), Deferrer, list_columns(), AsyncSession, Column, Protocol, Rebuild a list's stored calculated cells — the query index — from its formulas.…, Claim the right to re-index this list, or report that someone already has.… (+22 more)

### Community 45 - "List Config Bin Tests"
Cohesion: 0.10
Nodes (32): _age(), _create(), parametrize, The bin for a list's views, forms, metrics and columns. Until this surface…, A name is unique per list among *live* rows only, so the binned row's name is…, Past the retention window a row is due to be purged and is no longer promised…, Every child route is uuid-only now, but the bin is the one that could never…, A list delete stamps every child with the list's own `deleted_at` and its… (+24 more)

### Community 46 - "People & Sharing Screens"
Cohesion: 0.09
Nodes (33): Directory Is Separate From Access Grants, People & Groups Directory Screen, Read-Only Superuser Notice, Superuser Badge on User Row, User Search by Username or Email, Add Record Split-Button Menu, Fill Using QR Code, Submit Using QR Code (Disabled State) (+25 more)

### Community 47 - "List Item Service Reads"
Cohesion: 0.13
Nodes (17): AsyncSession, UUID, A single archived record. `get_by_uuid` filters them out, so unarchive needs…, The list's archive, most recently archived first, shaped for rendering so its…, Bring an archived record back into the list. None if it is not archived. Unlike…, The list's fields, as every read and write sees them. `in_shape` rather than…, `get_by_uuid` for a caller that will *render* the record, so its values come…, Present each record in its columns' declared shape. Two things happen here:… (+9 more)

### Community 48 - "Batch User & Import Dialogs"
Cohesion: 0.11
Nodes (18): Generated passwords leave exactly once, as a downloadable CSV, BatchUserDialog(), handleTemplate(), EXAMPLE, HEADERS, Props, credentialsCsv(), importErrorsCsv() (+10 more)

### Community 49 - "Auth Dependencies & Guards"
Cohesion: 0.15
Nodes (23): get_current_user(), AsyncSession, Guards account and group administration. Note this is orthogonal to the…, require_superuser(), create_access_token(), create_refresh_token(), decode_access_token(), get_password_hash() (+15 more)

### Community 50 - "Workspace Service Internals"
Cohesion: 0.13
Nodes (15): delete_workspace(), delete, AsyncSession, UUID, Workspace, A system workspace (the transfers inbox) is immutable: it cannot be renamed,…, Ensure a user has a workspace so they can create lists immediately. Called when…, The user's immutable "Incoming" workspace, minted on first use. Transferred… (+7 more)

### Community 51 - "Formula Evaluation Tests"
Cohesion: 0.06
Nodes (31): calc(), test_a_bare_comparison_reads_as_one_or_zero(), test_a_format_that_comes_from_a_cell_is_judged_per_row(), test_a_multiple_choice_cell_reads_as_its_labels_joined(), test_a_plain_date_formats_its_time_as_midnight(), test_a_quoted_string_literal_is_allowed(), test_age_of_a_future_date_is_negative_not_blank(), test_age_of_something_that_is_not_a_date_is_blank() (+23 more)

### Community 52 - "Record Archive Tests"
Cohesion: 0.11
Nodes (30): _archive(), parametrize, Archiving takes a record out of every normal read, and out of nothing else. The…, `after` names a position in the listing, so it has to name a record the listing…, The bin drops a record once it is past `PURGE_DELETED_AFTER_DAYS`, because the…, Delete wins. A record on its way out must not be hiding on a surface with no…, Maintenance runs over everything. An archived record that is then deleted and…, This is what makes `unarchive` incapable of conflicting, and it is the one… (+22 more)

### Community 53 - "Formula Language Tests"
Cohesion: 0.07
Nodes (24): The formula language for calculated columns, in isolation. This is the security…, test_a_formula_over_the_length_limit_is_rejected(), test_a_list_of_anything_but_labels_gives_no_value(), test_a_missing_operand_makes_the_result_blank_not_zero(), test_a_written_out_format_is_judged_when_the_formula_is_saved(), test_age_counts_whole_years(), test_age_is_a_number_so_it_takes_part_in_arithmetic(), test_an_oversized_text_result_is_blank_not_an_error() (+16 more)

### Community 54 - "Metrics Tests"
Cohesion: 0.12
Nodes (28): The compiled query is built on `visible_stmt`, so the filters still apply., test_a_filter_narrows_the_compiled_answer(), test_a_search_narrows_the_compiled_answer(), add_metric(), error_of(), parametrize, List metrics, over the HTTP API. A metric is a named formula stored beside its…, The other types store uuids or ISO strings whose text form would surprise — the… (+20 more)

### Community 55 - "List Routes & Conflicts"
Cohesion: 0.15
Nodes (27): ConflictError, A unique field (username, email, group name) is already taken., clone_list(), create_list(), delete_list(), get_list(), list_deleted_lists(), list_lists() (+19 more)

### Community 56 - "List Item Service Writes"
Cohesion: 0.13
Nodes (14): ListItemService, Column, Remove a now-dangling uuid from every record held by these columns. One UPDATE…, Reject a nested lookup value that does not belong to the parent's. Only values…, Whether a nesting's filter column is still a live column of the target list.…, Reject records that duplicate a value on a column marked unique. Runs after…, The raw values of a reference cell, as (multiple?, [non-empty values]). Blank…, A reference value read as a uuid, or None if it is a name/label. Trimmed only… (+6 more)

### Community 57 - "Metric Aggregate Language"
Cohesion: 0.11
Nodes (23): normalize_result(), A raw evaluation result as the one a caller may store or serialize. The rule…, call_kind(), _check(), column_refs(), fold(), MetricEvaluator, MetricFormula (+15 more)

### Community 58 - "Item Routes & Schemas"
Cohesion: 0.17
Nodes (26): archive_item(), bulk_archive_items(), bulk_delete_items(), count_items(), create_item(), get_item(), list_archived_items(), list_deleted_items() (+18 more)

### Community 59 - "Import/Export Guide Screens"
Cohesion: 0.11
Nodes (28): Export column selection (All / None, 13 of 13), Dual label + key column naming convention (Full name (full_name)), Export format choice (CSV / JSON), CSV headers use the column display name, not the key, Import / Export Dialog (guide screenshot), Which-records scope (Limit / Sort by / Direction), Home > Workspaces > Sales breadcrumb trail, Deleted lists (soft-delete recovery entry point) (+20 more)

### Community 60 - "Batch User Parsing Service"
Cohesion: 0.15
Nodes (23): ParsedFile, Turn the uploaded CSV into validated rows, without touching the database. Pure…, BatchUserError, BatchUserSummary, CreatedUser, import_users(), AsyncSession, UUID (+15 more)

### Community 61 - "Search & Recompute Locking"
Cohesion: 0.17
Nodes (26): session_factory(), Keyed on the list, not the caller — a second *person* gets the same answer., test_a_second_recompute_is_refused_while_one_holds_the_lock(), make_client(), _make_user(), fixture, Server-side search on the user/group/person listings. The People page and the…, `%` and `_` are LIKE wildcards; typed into a search box they must not act as… (+18 more)

### Community 62 - "User Settings Tests"
Cohesion: 0.14
Nodes (26): make_client(), _make_user(), fixture, Per-account UI preferences. Theme and light/dark used to live only in…, Two tabs changing different preferences must not clobber each other., A typo'd key should fail loudly rather than be stored and never read., Null is how the merge spells "not sent", so it must not be storable. The blob…, The likelier shape of a rollback than an unknown key: a deploy that adds a… (+18 more)

### Community 63 - "Metric Pushdown Tests"
Cohesion: 0.12
Nodes (24): _compiles(), _FakeColumn, parametrize, Metrics answered by Postgres instead of by a Python scan.…, The whole bargain: same question, same answer, whichever path runs it., Guards the test above from passing vacuously. If `compile_all` quietly stopped…, The three attributes `pushdown` reads off a column., Its stored value is an index that may lag a formula edit, so a total has to… (+16 more)

### Community 64 - "Metric SQL Pushdown"
Cohesion: 0.15
Nodes (23): _arithmetic(), compile_all(), _compile_call(), _number(), _parts(), _pushable(), AST, AsyncSession (+15 more)

### Community 65 - "People Service & Routes"
Cohesion: 0.19
Nodes (18): delete_person(), get_my_person(), get_person(), list_people(), AsyncSession, delete, get, patch (+10 more)

### Community 66 - "Add Column/Record Screens"
Cohesion: 0.11
Nodes (25): Add column dialog (guide screenshot), API name field (code-facing column name), BASICS / OPTIONS modal sectioning pattern, Max length option (optional text constraint), Text input mode toggle (Single line / Multiple lines / Rich text), Column type selector (Text default), Add record dialog (guide screenshot), Dependent reference gating ("Select Country first" disables City) (+17 more)

### Community 67 - "Dangling Lookup Target Sweep"
Cohesion: 0.13
Nodes (22): clear_dangling(), due(), _main(), Clear the config of lookup columns whose target list is already gone. A one-…, Every column the sweep would clear, as (list_id, uuid, name, dead target).…, Blank every dangling lookup's config. Returns the number of columns changed., _config(), dangling() (+14 more)

### Community 68 - "Choice 'Other' Option Tests"
Cohesion: 0.16
Nodes (21): parametrize, `allow_other` on a choice column, over the HTTP API. With it on, the column's…, The same deal a removed option strikes (known-gaps #4). Records outlive the…, A merged config PATCH must not leave a default the column now rejects., The payoff for storing the label itself: no filtering case of its own., A near-miss on an option is not a correction — it is a different answer.…, Anything but a string is a bad request, including one that cannot be hashed — a…, Not a validation error: blank means empty for every column type, decided in one… (+13 more)

### Community 69 - "Form Condition Tests"
Cohesion: 0.19
Nodes (23): _config(), _field(), form_uuid(), fixture, A form field's `condition` — the block that decides when the field applies. The…, The list's auto-provisioned create form., Empty means *always*, matching `apply_filters` on an empty filter group. A…, It is not a cycle — conditions are one pass over the draft's raw values — but a… (+15 more)

### Community 70 - "Live Event Stream Tests"
Cohesion: 0.11
Nodes (21): _count(), Live records: the broker, and the stream `GET /lists/{uuid}/events` serves. Two…, Without traffic a proxy reaps the connection and the server never learns the…, The feature, end to end: POST a record and the watcher is told to look again., The same guarantee across the other write paths, since each one is its own…, A 200-row import must cost one refetch, not 200. Half of that is the import…, A watcher woken for a write that did not happen refetches an unchanged page,…, `SSE_ENABLED=False` is the deployment's kill switch. It answers 503 rather than… (+13 more)

### Community 71 - "Sign-in & Toolbar Screens"
Cohesion: 0.13
Nodes (24): Username + Password Credential Form, Guide Screenshot Light/Dark Theme Pairing, Indigo Accent Design Language (rounded cards, single primary action), Keep Me Signed In (persistent session, default on), Password Reveal Toggle (eye icon), Sign-in Screen (guide screenshot), Edit Cells Mode Toggle, List Toolbar (guide screenshot) (+16 more)

### Community 72 - "Lookup Controls & Display Column"
Cohesion: 0.14
Nodes (19): CreateTargetRowDialog(), Props, PrimaryColumnLink, resolveDisplayColumn(), first, second, fallback(), labelQuery() (+11 more)

### Community 73 - "TypeScript Config"
Cohesion: 0.08
Nodes (23): compilerOptions, allowImportingTsExtensions, isolatedModules, jsx, lib, module, moduleDetection, moduleResolution (+15 more)

### Community 74 - "Batch Parsing Tests"
Cohesion: 0.14
Nodes (22): MAX_ROWS (1000-row cap), parse_file(), Validate the whole upload up front. Whole rather than streaming, deliberately…, parametrize, The file-format rules, with no database in sight. Everything here is pure:…, Blank lines must not shift the number a person reads in the error list., test_a_bad_email_is_a_row_error_not_a_file_error(), test_a_full_profile_is_validated_by_person_create() (+14 more)

### Community 75 - "Formula Parsing & Checks"
Cohesion: 0.12
Nodes (20): _parse_cached(), `parse`, remembered. Raises exactly as `parse` does — `lru_cache` does not…, _bad_directive(), _check(), check_row_expression(), eval_row_expression(), evaluate(), Formula (+12 more)

### Community 76 - "Account Security Tests"
Cohesion: 0.17
Nodes (22): make_client(), _make_user(), fixture, Account-administration guards. The permission model is only as strong as the…, Groups are principals in the grant table, so joining one inherits its grants., The virtual 'Authenticated Users' group exists only for the resolver., Reserved on the slug, which is what the resolver and every grant match on. The…, `name` is a wire identifier — a CSV's `groups` column and an exported `group`… (+14 more)

### Community 77 - "Column Settings Screens"
Cohesion: 0.14
Nodes (23): Per-column row actions: edit, convert, duplicate, delete, Column type catalog (Text, Choice, Date, Number, Lookup), Contacts demo schema (Full name, Email, Status, Birthday, Country, City, Deal value, Notes, Commission), Lookup column (link to another list), Guide screen: Contacts settings — Columns tab, List settings tab bar (Columns / Permissions / Views / Forms / Metrics), Light/dark theme variants of the guide screenshots, "Also copy the records" opt-in checkbox (+15 more)

### Community 78 - "Guided Tour & Help"
Cohesion: 0.15
Nodes (18): HelpWidget(), Box, placeCard(), Props, same(), TourOverlay(), useAnchorBox(), useCardSize() (+10 more)

### Community 79 - "Live Stream Client"
Cohesion: 0.16
Nodes (20): LIVE_REFRESH_SECONDS, ACTIVITY_EVENTS, bindActivity(), Channel, channels, emitChange(), frameEvent(), LiveStatus (+12 more)

### Community 80 - "Record Paging Tests"
Cohesion: 0.12
Nodes (21): Paging, ordering and the total count of the live records listing. The listing…, Clicking a header means "order by this", not "then by this"., The count and the page must answer the same question, or the paginator offers…, A calculated column is stored, so ordering by it is an ordinary `data ->> key`.…, `count` records, named so that lexical order is also insertion order., One list, two formulas, no declared output type. The language decides the type…, The bug itself: more records than the endpoint's default `limit`. Kept just…, The reason ordering had to move server-side. Sorting descending must make the… (+13 more)

### Community 81 - "List Transfer Tests"
Cohesion: 0.21
Nodes (20): make_client(), _make_list(), _make_user(), fixture, List ownership transfer. Ownership flows through the workspace, so handing a…, Owner-only — even a `manage` grant on the list does not qualify., A grant points at the list by resource_id, unchanged by the move, so a user the…, Two lists of the same name transferred to one inbox: the second lands renamed… (+12 more)

### Community 82 - "Login Throttle Tests"
Cohesion: 0.14
Nodes (20): limits(), _login(), _make_user(), fixture, Login is the only route that answers before the caller has proved anything.…, Each username stays under its own limit, so only the address bucket sees what…, Only the rightmost entry counts — the rest is whatever the caller sent., The escape hatch for a deployment where every user shares one apparent address… (+12 more)

### Community 83 - "Workspace Transfer Tests"
Cohesion: 0.21
Nodes (20): make_client(), _make_user(), _make_workspace(), fixture, Workspace ownership transfer. A workspace *is* the ownership boundary —…, `shared=false` is "workspaces you own", so the recipient's own listing must…, Owner-only — even a `manage` grant on the workspace does not qualify., A grant points at the workspace by resource_id, which the transfer does not… (+12 more)

### Community 84 - "Frontend Runtime Dependencies"
Cohesion: 0.10
Nodes (21): @dnd-kit/core, @dnd-kit/sortable, dependencies, @dnd-kit/core, @dnd-kit/sortable, html5-qrcode, libphonenumber-js, primeicons (+13 more)

### Community 85 - "Frontend Dev Dependencies"
Cohesion: 0.10
Nodes (21): devDependencies, openapi-typescript, @playwright/test, tailwindcss, @tailwindcss/vite, @types/react, @types/react-dom, typescript (+13 more)

### Community 86 - "Location Field & Geocoding"
Cohesion: 0.20
Nodes (15): GeocodeResponse, GeocodeResult, LocationField(), choose(), commit(), place(), asLocation(), defaultView() (+7 more)

### Community 87 - "Login Rate Limiting"
Cohesion: 0.15
Nodes (19): clear_failures(), client_ip(), _ip_key(), Request, Rate limiting for the login endpoint. Login is the one route that has to answer…, Count one wrong password against both the username and the address., Forget a username's failures after it is signed in successfully. The address…, Evict buckets that have gone quiet. Only walks the dict once it is big enough… (+11 more)

### Community 88 - "Bin Maintenance Helpers"
Cohesion: 0.18
Nodes (19): count_deleted(), _cutoff(), get_deleted(), list_deleted(), owned_workspaces(), purge(), AsyncSession, datetime (+11 more)

### Community 89 - "Bin Service"
Cohesion: 0.17
Nodes (18): count_deleted(), _cutoff(), get_deleted(), list_deleted(), purge(), AsyncSession, datetime, UUID (+10 more)

### Community 90 - "Formula Runtime Functions"
Cohesion: 0.13
Nodes (20): _age(), _compare(), _dt_format(), _eval(), _is_num(), _now(), date, datetime (+12 more)

### Community 91 - "Location Column Tests"
Cohesion: 0.16
Nodes (19): _add_location_column(), parametrize, The `location` column type, over the HTTP API. **This is the first cell in the…, The reason `_field` has a branch: `->>` on an object is `{"address": …}`, so…, The canonical text form is what makes a location column round-trip through a…, A conversion is the point of no return, so a dict repr baked into every record…, JSONB containment compares objects natively, so `unique` needed nothing., test_a_broken_config_is_refused_at_config_time() (+11 more)

### Community 92 - "Name Slugification"
Cohesion: 0.13
Nodes (16): Turn free text into a `NameSlug` (`^[a-z][a-z0-9_]*$`, see core/schemas.py).…, slugify_name(), parametrize, `slugify_name` and the group backfill's dedupe, tested away from Postgres. The…, Two distinct free-text names can slugify identically, and `name` is unique., The suffix is why the migration slugifies to less than the full width., Whatever goes in, what comes out is something the API would accept., Not transliteration — the letters are dropped, not romanised. What matters is… (+8 more)

### Community 93 - "Geocode Proxy"
Cohesion: 0.22
Nodes (16): geocode(), GeocodeResponse, get, Candidates for a typed address. Never fails: an unconfigured or unreachable…, GeocodeResponse, GeocodeResult, One candidate for a typed address. Deliberately three fields and no more.…, _cache_get() (+8 more)

### Community 94 - "View Filtering Tests"
Cohesion: 0.15
Nodes (17): _is_num(), The column itself is refused, rather than one that can never be filled in., test_temporal_range_must_be_well_formed(), _pin_utc(), Server-side filtering and sorting of records via a view's config., Put the acting user in UTC, so "today" here and on the server are one day.…, A `date` condition written as "today" means the day the query runs. The point…, A `datetime` takes both words, and "today" is that day's midnight. Which is… (+9 more)

### Community 95 - "Multiple Choice Tests"
Cohesion: 0.16
Nodes (18): _column(), parametrize, A `multiple` choice column's **default**, which is the one part of the setting…, Verbatim, including the empty one. `[]` is **not** collapsed to `None` the way…, The other half of storing `[]` as itself: it still reads as blank. Both…, Create a choice column, returning the response rather than the uuid: half of…, The reason the dialog holds a separate piece of state for this shape. A default…, The mirror, so neither direction is left to chance. (+10 more)

### Community 96 - "List Config Purge Tests"
Cohesion: 0.18
Nodes (18): _age(), _count(), _create(), Purging soft-deleted views/forms/metrics after the retention window. The job…, The predicate is `deleted_at IS NOT NULL`, so a live view is never a candidate…, A list delete stamps every child with the list's own `deleted_at`. Those rows…, The hazard the `JOIN lists` exists for. This job takes a `--days` of its own,…, `permission_grants` has no foreign key to its resource, so hard-deleting the… (+10 more)

### Community 97 - "Theme & Figures"
Cohesion: 0.15
Nodes (15): ThemeSwitcher(), Figure(), Figure, FIGURES, readMode(), readTheme(), THEME_IDS, ThemeContext (+7 more)

### Community 98 - "Conversion Service Internals"
Cohesion: 0.20
Nodes (11): AsyncSession, Column, ColumnConfig, ColumnType, datetime, Enough of the list's state to tell whether anyone else wrote to it. `(count,…, Check the pair is convertible and return the config to store. Shared by…, One pass over every record, converting the source cell. ``keys`` is ``None``… (+3 more)

### Community 99 - "Duration Column Tests"
Cohesion: 0.18
Nodes (16): _add_duration_column(), parametrize, The `duration` column type, over the HTTP API. A cell holds **seconds**, and…, 1:30" is an hour and a half on an h:mm column and ninety seconds on an m:ss one…, 1:75" is a typo, not a silent 2:15., Total hours" is the motivating metric, and it is why duration is in…, A list is deliberately absent from this table: `coerce_shape` takes the first…, test_a_cell_holds_seconds() (+8 more)

### Community 100 - "Phone Column Tests"
Cohesion: 0.18
Nodes (16): _add_phone_column(), parametrize, The `phone` column type, over the HTTP API. The normalisation is the type. A…, The motivating conversion: a free-text phone column, tidied into one form, with…, The whole point: without normalising, these are two values., No server-side geography guess: a column that was never told which country a…, test_a_broken_config_is_refused_at_config_time(), test_a_national_number_needs_a_region_to_be_read_at_all() (+8 more)

### Community 101 - "Person Model & Routes"
Cohesion: 0.21
Nodes (13): _person_from(), The row's profile, or None when it has none. Every person cell blank means an…, Person, A user's personal details, kept off the account row itself. One profile per…, create_person(), post, Creates the caller's own profile. A superuser may create one for someone else…, _not_in_the_future() (+5 more)

### Community 102 - "User Settings Schemas"
Cohesion: 0.13
Nodes (14): patch, UserSettingsUpdate, Save UI preferences against the account so they follow the user to any device.…, update_my_settings(), Any, field_validator, model_validator, Refuse a zone this build's tz database cannot resolve. `theme` and `mode` get… (+6 more)

### Community 103 - "Search & UUID Utilities"
Cohesion: 0.13
Nodes (11): Shared by list() and count() so the page and its total can never disagree.…, coerce_uuid(), like_pattern(), UUID, Best-effort parse of a value into a UUID, returning None if malformed., Build a `%term%` ILIKE pattern from a search box. `%` and `_` are wildcards in…, delete_item(), delete (+3 more)

### Community 104 - "Item Update & Row Lock"
Cohesion: 0.17
Nodes (13): _backfill_section_counts(), Write each section's headcount onto it, so `open_slots` means something.…, patch, update_item(), ListItemUpdate, `for_update` locks the row until the transaction commits, for a caller that…, Archive one record. False if it is not a live record of this list., _list_id() (+5 more)

### Community 105 - "Schema Drift Check"
Cohesion: 0.20
Nodes (15): _enum_types(), find_drift(), Connection, raise_on_drift(), Startup guard against schema drift. Alembic owns the schema, but it does not…, The database is missing something the models expect., Every named enum type the models use, mapped to its expected labels., Return remediation SQL for everything the models expect but the DB lacks. Runs… (+7 more)

### Community 106 - "Conversion Scratch Cleanup"
Cohesion: 0.15
Nodes (15): drop_scratch(), _main(), Destroy the hidden scratch columns the old staged conversion left behind. A…, Destroy every hidden column and the record values it owns. Returns the count., fixture, The one-off that clears what the old staged conversion left behind. Column…, Safe to run twice, and safe to leave in a runbook. A second pass finds nothing…, Two per batch over five columns, so the loop runs several times and the last… (+7 more)

### Community 107 - "Migration Tests"
Cohesion: 0.19
Nodes (16): _alembic_config(), _downgrade_base(), The migrations must reproduce exactly what the models describe. Two ways this…, Stronger than find_drift for tables/columns: it also catches *extra* schema, so…, lists.primary_column_id -> columns.id is use_alter (the two tables reference…, The data half of merge_long_text_into_text. Every other migration test starts…, Revisions must be reversible, not write-only., Catches a model change with no revision, and a model missing from env.py. (+8 more)

### Community 108 - "Token Revocation Tests"
Cohesion: 0.25
Nodes (16): _login(), _make_user(), Signing out has to end the session on the server, not only in the browser.…, The SPA fires this without waiting for it, so a retry or a double-click must…, Signing out is not a failed sign-in. If the revoke ever routed through the…, The session most in need of a sign-out is one already past its access token.…, Signing out of one browser must not sign the account out everywhere. Each login…, 204 whether or not the token existed. A sign-out has nothing to do with the… (+8 more)

### Community 109 - "App Factory & Health"
Cohesion: 0.16
Nodes (12): create_app(), lifespan(), ensure_system_groups(), AsyncSession, Idempotently create the virtual groups the resolver relies on. Currently just…, main(), Print the API's OpenAPI document to stdout. uv run python -m…, The liveness probe a host health-checks. It is the one endpoint that must… (+4 more)

### Community 110 - "Backend README & Demos"
Cohesion: 0.14
Nodes (16): Database with UI backend (FastAPI API), Guide demo scenario (Sales/Contacts), School demo scenario, Tests run against a real PostgreSQL database, All record validation lives in the service layer, Pre-paint theme bootstrap script, The capture run is the only check on the guide's prose, The client deliberately does not restate server validation (+8 more)

### Community 111 - "Formula & Naming Docs"
Cohesion: 0.16
Nodes (16): A calculated cell is a query index, not the source of truth, DT_FORMAT rendered from an explicit directive table, Calculated columns: the formula language, Metric aggregate grammar layered on the row formula language, A missing operand blanks the whole formula result, name (slug) vs display_name, and uuid-only URLs, recompute_calculated maintenance job, Metric and count refresh floors bound the polling cost (+8 more)

### Community 112 - "Unique + Multiple Guard Tests"
Cohesion: 0.15
Nodes (15): _create(), `unique` and `multiple` may not be configured together. They used to be…, The same collision from the other side., Refusing the pair must not make a column that holds it unfixable. Sending both…, No retro-validation, so columns that predate this check keep both settings and…, The refusal is about the pair. Neither setting is restricted on its own., `multiple` belongs to every reference type too, and `unique` is common to all…, The check runs after the config merge, so a PATCH that resends only the setting… (+7 more)

### Community 113 - "Guide Screenshot Capture"
Cohesion: 0.19
Nodes (15): apiGet(), apiLogin(), apiPatch(), HERE, main(), makeCtx(), MANIFEST, MODES (+7 more)

### Community 114 - "CSV Upload Route"
Cohesion: 0.14
Nodes (13): import_users_csv(), AsyncSession, post, UploadFile, Create many accounts from one CSV. Superuser-only, like every other account…, Protocol, Reading an uploaded file without letting it decide how much memory to use.…, The only part of an uploaded file this needs: bytes, on request. Typed… (+5 more)

### Community 115 - "Calculated Column Tests"
Cohesion: 0.13
Nodes (5): The `calculated` column type, over the HTTP API. A calculated column holds a…, The point of the change: there is a real `data ->> key` for Postgres to see., The reason the export used to leave calculated columns out. The file now…, test_an_exported_file_imports_straight_back(), test_the_value_is_stored_not_only_computed()

### Community 116 - "Geocode Tests"
Cohesion: 0.18
Nodes (12): _clear_cache(), no_wait(), fixture, The address-search proxy behind the location picker. Nothing here reaches the…, The cache is process-global, so a test that seeds it would leak into the next., Drop the one-call-a-second spacing. It is real and deliberate (Nominatim's…, Stand in for the geocoder by replacing the `httpx.AsyncClient` *class*. Not by…, Nominatim's response carries bounding boxes, OSM ids and licensing. None of it… (+4 more)

### Community 117 - "Rating Column Tests"
Cohesion: 0.20
Nodes (14): _add_rating_column(), parametrize, The `rating` column type, over the HTTP API. A rating is a number in a costume,…, The whole reason `NUMERIC_TYPES` exists: as text, 10 sorts before 9., Zero included: an unrated record is an empty cell, not a nought., A configurable floor would give the column two ways to say "unrated"., test_a_metric_can_average_it(), test_a_rating_column_has_no_min() (+6 more)

### Community 118 - "Timestamp Mixins"
Cohesion: 0.27
Nodes (7): DatetimeTrackerMixin, DateTime, declared_attr, Mapped, UUID, In the bin. A read schema can expose this without leaking *when* it went, which…, UUIDMixin

### Community 119 - "People CRUD Tests"
Cohesion: 0.27
Nodes (13): make_client(), _make_user(), fixture, Person (profile) CRUD. A person record is personal data, so it is readable only…, The unique index is partial on deleted_at IS NULL., A field added to PersonBase must actually reach the row — `create` used to list…, test_birth_date_cannot_be_in_the_future(), test_create_read_update_delete_own_person() (+5 more)

### Community 120 - "Lifecycle & Sharing Docs"
Cohesion: 0.15
Nodes (13): Archive as a second lifecycle axis, Every soft-deletable kind has a bin on its own collection, BULK_DELETE_MAX is a published limit the SPA mirrors, Chunked purge loop shared with permanent routes, Live records SSE stream, Sharing: grants with a downward-implying action vocabulary, SSE_MAX_STREAM_SECONDS is the revocation window, SSE_ENABLED kill switch lives on the API, not the SPA (+5 more)

### Community 121 - "Test Fixtures & Engine"
Cohesion: 0.21
Nodes (12): _clean_login_throttle(), engine(), _ensure_database(), list_uuid(), fixture, Test fixtures. Tests run against a real PostgreSQL database (the models rely on…, Create the test database if it does not exist yet., Login failure counters live in the process, not in the database, so the per-… (+4 more)

### Community 122 - "Calculated Reference Tests"
Cohesion: 0.15
Nodes (13): _add_calculated(), What the function is for: a cell that says whether this row is past its date., test_a_calculated_column_can_reference_another(), test_a_formula_nested_too_deeply_is_rejected(), test_a_multiple_choice_column_reads_as_its_labels_joined(), test_a_text_column_can_be_referenced_and_produce_text(), test_a_written_out_format_that_makes_no_sense_is_refused_at_save(), test_age_over_a_date_column() (+5 more)

### Community 123 - "Cell Patch Tests"
Cohesion: 0.19
Nodes (12): contact_list(), _create(), fixture, One cell at a time: the record PATCH as an editable grid uses it. Editing a…, A commit with nothing in it is a no-op rather than an error — but note that it…, A list with three independent columns, so a write to one is visible as a write…, Emptying a cell is `null` for that key, and says nothing about the rest. This…, Required is enforced over the *merged* record, so a single-cell clear is… (+4 more)

### Community 124 - "Text Mode Tests"
Cohesion: 0.21
Nodes (10): _add_text_column(), parametrize, `text_mode` on a text column, over the HTTP API. One line versus several is how…, Unset means single line, so an existing column needs no backfill., The whole reason this is config: a stored value survives the switch., Formulas took `text` and `long_text`; the merge must not narrow that., test_accepts_each_mode(), test_mode_can_change_after_creation() (+2 more)

### Community 125 - "Database Session Setup"
Cohesion: 0.20
Nodes (11): async_sessionmaker, AsyncEngine, Base, get_db(), init_db(), make_engine(), make_session_factory(), AsyncSession (+3 more)

### Community 126 - "Color Column Tests"
Cohesion: 0.26
Nodes (9): _add_color_column(), parametrize, The `color` column type, over the HTTP API. A colour has two spellings of the…, A palette is a shortcut, not an option set — the point of a colour field is…, test_a_colour_is_stored_canonically(), test_accepts_a_hex_colour(), test_rejects_anything_that_is_not_rrggbb(), test_swatches_offer_rather_than_restrict() (+1 more)

### Community 127 - "Email Column Tests"
Cohesion: 0.26
Nodes (10): _add_email_column(), parametrize, The `email` column type, over the HTTP API. The load-bearing property is not…, The whole point of normalizing: without it these are two values., test_accepts_an_address(), test_an_address_is_stored_trimmed_and_lowercased(), test_import_normalizes_and_rejects_row_by_row(), test_max_length_is_measured_on_the_stored_form() (+2 more)

### Community 128 - "Link Column Tests"
Cohesion: 0.24
Nodes (10): _add_link_column(), parametrize, The `link` column type, over the HTTP API. The scheme check is a security…, The check parses the trimmed URL, so the trimmed URL is what gets stored —…, Import goes through the same validation, so a CSV is not a side door., test_a_url_is_stored_trimmed(), test_accepts_an_http_url(), test_allowed_schemes_narrows_but_cannot_widen() (+2 more)

### Community 129 - "Render Deployment Blueprint"
Cohesion: 0.20
Nodes (11): Placeholder auth settings for maintenance jobs, No container: native runtime plus .python-version, Render deployment runbook, render.yaml blueprint, dbui-api (Python web service, starter), dbui-db (Postgres, basic-256mb), dbui environment variable group, The paid database buys a life longer than 30 days (+3 more)

### Community 130 - "Group Slug Migration"
Cohesion: 0.24
Nodes (10): _dedupe(), downgrade(), a group's name becomes a slug, its label moves to display_name…, Fold display_name back into name and drop the column. Reversible, unlike…, A frozen copy of src.core.utils.slugify_name. Copied rather than imported…, `slug`, or the first free `slug_2`, `slug_3`, ... after it. Both directions…, _slugify(), upgrade() (+2 more)

### Community 131 - "Deployment Config Docs"
Cohesion: 0.18
Nodes (11): /docs, /redoc and /openapi.json are DEBUG-gated, Geocode proxy for location columns, Login failure throttle, per username and per IP, The API is public by design, protected by the bearer token, dbui-web (static SPA site), DISABLE_AUTH declared False rather than absent, /api/* rewrite makes SPA and API share an origin, One instance is load-bearing for the login throttle (+3 more)

### Community 132 - "Live Event Fixtures"
Cohesion: 0.18
Nodes (11): Forget the shutdown flag, so the next stream starts from a clear one. For…, reset_shutdown(), app_client(), live_list(), fixture, A list with one text column. Returns `(list uuid, column uuid, list id)`. The…, Turn `DISABLE_AUTH` off for one test, so tokens are actually read. The rest of…, A client with no ambient Authorization header, so each call brings its own. (+3 more)

### Community 133 - "App Settings"
Cohesion: 0.20
Nodes (7): model_validator, DISABLE_AUTH is a development shortcut and a production disaster. It does not…, Parse the comma-separated ALLOWED_ORIGINS env value into a list., Settings, DISABLE_AUTH does not just skip the token check — it resolves every caller to…, test_auth_cannot_be_disabled_outside_debug(), BaseSettings

### Community 134 - "School Principal Seeding"
Cohesion: 0.22
Nodes (10): build_principals(), date, Three to five subjects per active learner, always at their own year level.…, Every school day in a span — Monday to Friday, which is what an attendance…, Accounts, their personal details, and the groups access is granted through., _seed_enrollments(), _seed_student_services(), _seed_students() (+2 more)

### Community 135 - "Record Purge Tests"
Cohesion: 0.29
Nodes (9): _age(), Purging soft-deleted records after the retention window. Deleting a record only…, `--days 0` means "empty the bin now" — the escape hatch for someone who needs…, Backdate a record's deletion, so the retention window can be tested without…, Nothing else removes these. `permission_grants` points at its resource with no…, _rows(), test_a_zero_day_window_purges_everything_deleted(), test_only_records_past_the_window_are_destroyed() (+1 more)

### Community 136 - "Maintenance Workflow Docs"
Cohesion: 0.31
Nodes (9): Daily maintenance workflow, Maintenance runs in GitHub Actions, not Render Cron, Maintenance job ordering rule, Grants point at resources without a foreign key, purge_deleted_items maintenance job, purge_deleted_list_config maintenance job, purge_deleted_lists maintenance job, purge_deleted_workspaces maintenance job (+1 more)

### Community 137 - "Batch Accounts & Tenancy Docs"
Cohesion: 0.22
Nodes (9): Batch account creation from CSV, A generated password exists only in the import response, purge_deleted_principals maintenance job, CSV/JSON import and export keyed by column name, Workspaces as the tenancy boundary, QrScanDialog is lazy-loaded to keep the decoder out of the bundle, qrFill: QR JSON keyed by column name, QR submitting session (+1 more)

### Community 138 - "Column Type Docs"
Cohesion: 0.25
Nodes (9): Column type conversion in place, Column types and per-column JSONB config, reconcile_shapes maintenance job, The eight files a new column type must teach, libphonenumber-js paired with the backend's phonenumbers, safeHref guards a link cell's URL, Alembic owns the schema; the app only verifies it, Alembic autogenerate caveats (enum values, use_alter FKs) (+1 more)

### Community 139 - "Stream Subscribe & Shutdown"
Cohesion: 0.22
Nodes (8): begin_shutdown(), The process-wide "stop streaming" flag, created on first use., End every open stream. Called from the lifespan's shutdown half., Watch one list. Yields the event that `notify` will set. A context manager so…, shutdown_event(), Otherwise a stream is an in-flight request forever and uvicorn's graceful…, test_shutdown_ends_an_open_stream(), Event

### Community 140 - "List Event Broker"
Cohesion: 0.22
Nodes (6): ListEventBroker, Wakes every stream watching a list when that list's records change., Tell everyone watching this list to look again. Call it **after the write has…, How many streams are watching this list. For tests and diagnostics., The property the Event mailbox exists for, and the reason it is not a queue. An…, test_a_burst_of_notifies_coalesces_into_one_wake_up()

### Community 141 - "List Config Purge Job"
Cohesion: 0.39
Nodes (8): _count(), _main(), purge(), _purge_table(), AsyncSession, datetime, Destroy a list's deleted views, forms, metrics and columns once past the…, Destroy soft-deleted views/forms/metrics/columns older than ``days``. Returns…

### Community 142 - "Calculated Column Fixtures"
Cohesion: 0.22
Nodes (9): _add_number(), priced_list(), fixture, A list with `qty` and `price` number columns and a `total` formula., test_a_calculated_column_named_like_a_function_can_be_referenced(), test_a_chain_settles_in_one_write_whatever_order_the_columns_were_made(), test_a_formula_cannot_form_a_cycle(), test_a_formula_cannot_reference_itself() (+1 more)

### Community 143 - "Filter Token Resolution"
Cohesion: 0.31
Nodes (8): `today`/`now` in a filter, resolved without a database. The end-to-end…, Not the server's date -- the difference is a whole day for a third of them.…, The distinction the datetime column exists to make. `>= today` is the rest of…, An id renamed or dropped between tzdata releases. Wrong by an offset beats a…, test_a_datetime_resolves_today_to_midnight_and_now_to_the_reading(), test_an_unknown_zone_does_not_stop_the_filter(), test_today_is_the_date_where_the_user_is(), _user()

### Community 144 - "Soft Delete Reversibility"
Cohesion: 0.42
Nodes (8): _age_item(), _cell(), Soft delete destroys nothing; purge destroys everything. The scrub used to run…, sa_update_user(), _target(), test_a_deleted_account_keeps_its_cells_until_it_is_finalized(), test_a_deleted_row_keeps_its_inbound_reference_and_stays_labelled(), test_purging_the_row_is_what_clears_the_reference()

### Community 145 - "Frontend NPM Scripts"
Cohesion: 0.22
Nodes (9): scripts, build, capture:guide, dev, gen:api, gen:api:spec, preview, test (+1 more)

### Community 146 - "Nav Tree Component"
Cohesion: 0.22
Nodes (3): NavLinkProps, NavTree(), Props

### Community 147 - "Alembic Environment"
Cohesion: 0.32
Nodes (7): do_run_migrations(), Connection, # NOTE: autogenerate still cannot see new *enum values* — it diffs tables and, Emit SQL to stdout instead of running it (`alembic upgrade head --sql`)., run_async_migrations(), run_migrations_offline(), run_migrations_online()

### Community 148 - "Demo Sharing Grants"
Cohesion: 0.25
Nodes (8): grant(), datetime, ResourceType, UUID, Share a resource with one principal. Already idempotent at the service: there…, build_sharing(), _find_form(), Who can see what. Every resource type a grant can target appears here —…

### Community 149 - "List Purge Job"
Cohesion: 0.36
Nodes (7): _count(), _main(), purge(), AsyncSession, datetime, Destroy lists that have been in the bin longer than the retention window.…, Destroy soft-deleted lists older than ``days``. Returns how many went.

### Community 150 - "Workspace Purge Job"
Cohesion: 0.36
Nodes (7): _count(), _main(), purge(), AsyncSession, datetime, Destroy workspaces that have been in the bin longer than the retention window.…, Destroy soft-deleted workspaces older than ``days``. Returns how many went.

### Community 151 - "Workspace Dependencies"
Cohesion: 0.36
Nodes (6): get_workspace_or_404(), AsyncSession, UUID, Workspace, Resolve a workspace the current user can view, or raise 404/403., Workspace

### Community 152 - "List Purge Tests"
Cohesion: 0.43
Nodes (7): _age_list(), _count_table(), Purging soft-deleted lists and their descendants/grants after the retention…, Backdate a list's deletion, and its descendants' deletion, so the retention…, test_a_zero_day_window_purges_everything_deleted(), test_descendants_and_grants_go_with_it(), test_only_lists_past_the_window_are_destroyed()

### Community 153 - "Workspace Purge Tests"
Cohesion: 0.43
Nodes (7): _age_workspace(), _count_table(), Purging soft-deleted workspaces and all their lists/descendants/grants after…, Backdate a workspace's deletion, and its lists and descendants' deletion, so…, test_a_zero_day_window_purges_everything_deleted(), test_only_workspaces_past_the_window_are_destroyed(), test_workspace_descendants_and_grants_go_with_it()

### Community 154 - "Event Stream Route"
Cohesion: 0.43
Nodes (6): _authorize(), list_events(), get, UUID, `GET /lists/{uuid}/events` — the stream an open list holds while it is on…, Resolve the caller and their `view` on the list. Returns the list's id. Every…

### Community 155 - "Visible Record Query"
Cohesion: 0.29
Nodes (4): A list's *normal* records: neither binned nor archived. `visible_stmt` is built…, The records this caller may see, through this view, matching this search.…, How many records `list` would return without a page — the paginator's total.…, Select

### Community 156 - "Error Boundary"
Cohesion: 0.29
Nodes (3): ErrorBoundary, Props, State

### Community 157 - "Demo World Helper"
Cohesion: 0.33
Nodes (3): What has been built so far, so later phases can reference earlier ones. Lists…, One record's uuid, or None if the list came out shorter than generated. Only…, World

### Community 158 - "Workspace List Bin"
Cohesion: 0.33
Nodes (3): Lists in a workspace's bin: soft-deleted and still inside the retention window.…, A workspace's list bin, most recently deleted first., How many lists are in the workspace's bin — the paginator's total, built off…

### Community 159 - "List Bin Tests"
Cohesion: 0.33
Nodes (4): The lists bin: a workspace's soft-deleted lists can be listed and restored, and…, A restore must bring the list back configured, not blank. The delete scrub…, test_a_lookup_at_the_deleted_list_itself_is_not_scrubbed(), test_list_bin_lists_and_restores()

### Community 161 - "CSV Header Validation"
Cohesion: 0.40
Nodes (5): check_headers(), KNOWN_HEADERS (the twelve CSV columns), Reject a file whose columns are wrong, before a single account exists. Unknown…, Finding #9 — a server-stamped onupdate column is disabled, not absent, Unknown headers rejected because the column set is closed

### Community 162 - "Batch User Row Schema"
Cohesion: 0.50
Nodes (3): BatchUserRow, field_validator, One validated line of the upload. Carries a fully-validated ``PersonCreate``…

### Community 163 - "Calculated Index Corruption"
Cohesion: 0.40
Nodes (5): _corrupt_index(), Write a wrong value straight into the record, the way a stale index looks., The load-bearing guarantee. A stale index must never reach a person. Both read…, test_a_read_recomputes_over_a_stale_stored_value(), test_recompute_rebuilds_the_index()

### Community 164 - "Formula Clock Fixtures"
Cohesion: 0.40
Nodes (5): clock(), fixture, Pin the date `AGE` counts to, and hand it back. `_today` exists as a seam for…, Pin the reading `TODAY()` and `NOW()` answer with, and hand it back. `_now` is…, today()

### Community 165 - "Archive Test Fixtures"
Cohesion: 0.40
Nodes (5): ledger(), linked(), fixture, A `schools` list with one row, and a lookup at it from `list_uuid`., Four records, one of which gets archived by the tests that need it. `title` is…

### Community 166 - "Metric Pushdown Fixtures"
Cohesion: 0.40
Nodes (5): ledger(), no_pushdown(), fixture, Force the Python scan, whatever the formula would otherwise allow., Numbers, text, a calculated column, and rows that exercise the edges. One…

### Community 168 - "Session Config Tests"
Cohesion: 0.40
Nodes (3): Regression guards for the database session configuration. A production-vs-test…, The behavioral guard: build an object, commit, then read a mapped attribute.…, test_attribute_access_after_commit_does_not_raise()

### Community 169 - "Unique Concurrency Tests"
Cohesion: 0.40
Nodes (4): Prove the per-list advisory lock actually serializes writes. Driving two…, Functional companion: a duplicate on a unique column returns 409., test_create_blocks_while_list_advisory_lock_is_held(), test_sequential_unique_still_conflicts()

### Community 170 - "Plan & Spec Documents"
Cohesion: 0.40
Nodes (5): Inline Cell Editing Review Findings — Implementation Plan, Batch user creation Implementation Plan, A group gets a slug name and a human display_name (design), Closing the inline cell editing review findings (design), Batch user creation from a CSV file (design)

### Community 171 - "Editability Predicate Findings"
Cohesion: 0.50
Nodes (5): cellMemo={false} (measured, with editMode gating as fallback), Finding #14 — one editability predicate per column, Finding #8 — the stale permission closure in PrimeReact's cell memo, useEffectiveActionsMap / ActionsMap.ready, isEditable (per-column predicate)

### Community 172 - "Frontend Package Identity"
Cohesion: 0.40
Nodes (4): name, private, type, version

### Community 173 - "Deployment Invariants"
Cohesion: 0.50
Nodes (4): External DATABASE_URI with ssl=require, Internal URL for the API, external URL for maintenance, DATABASE_URI is hand-set and cannot use fromDatabase, Host-independent deployment invariants

### Community 179 - "Formula Rejection Tests"
Cohesion: 0.50
Nodes (4): parametrize, test_a_deeply_nested_formula_is_rejected_not_a_crash(), test_int_reads_a_whole_number_out_of_text(), test_rejects_anything_outside_the_language()

### Community 200 - "Pydantic Error Flattening"
Cohesion: 0.67
Nodes (3): _flatten(), Pydantic's error list as one line, keeping the field name that failed., PydanticValidationError

### Community 201 - "Conversion Test Client"
Cohesion: 0.67
Nodes (3): make_client(), fixture, A client authenticated as somebody else — see test_lookup_grants.

### Community 202 - "Metric Test Fixtures"
Cohesion: 0.67
Nodes (3): fixture, A list with `hours` and `rate` numbers, an `owner` text, and three records., timesheet()

### Community 203 - "Per-row Transaction Handling"
Cohesion: 0.67
Nodes (3): Each row is already its own transaction (no savepoint machinery), Skip-and-report per-row error handling, scheduleSettle (debounced invalidate)

### Community 204 - "Frontend Layout Conventions"
Cohesion: 0.67
Nodes (3): AppDialog: buttons belong in the footer band, One NavTree for both rail and drawer, SMALL_SCREEN and DESKTOP_SCREEN breakpoints

## Ambiguous Edges - Review These
- `Every soft-deletable kind has a bin on its own collection` → `BULK_DELETE_MAX is a published limit the SPA mirrors`  [AMBIGUOUS]
  backend/README.md · relation: conceptually_related_to
- `saveCell mutation` → `ListItemService._reference_matches`  [AMBIGUOUS]
  docs/superpowers/specs/2026-07-31-inline-cell-editing-findings-design.md · relation: conceptually_related_to
- `Column type selector (Text default)` → `Dependent reference gating ("Select Country first" disables City)`  [AMBIGUOUS]
  frontend/public/guide/add-record-dark.png · relation: conceptually_related_to
- `Granular Permission Chips (View, Create, ...)` → `Submit Using QR Code (Disabled State)`  [AMBIGUOUS]
  frontend/public/guide/qr-menu-dark.png · relation: conceptually_related_to

## Knowledge Gaps
- **228 isolated node(s):** `database-w-ui`, `name`, `private`, `version`, `type` (+223 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **27 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Every soft-deletable kind has a bin on its own collection` and `BULK_DELETE_MAX is a published limit the SPA mirrors`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `saveCell mutation` and `ListItemService._reference_matches`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Column type selector (Text default)` and `Dependent reference gating ("Select Country first" disables City)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Granular Permission Chips (View, Create, ...)` and `Submit Using QR Code (Disabled State)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `add_column()` connect `List HTTP Flow Tests` to `Conversion Regression Tests`, `Record Purge Tests`, `Lookup Options & Label Search`, `Soft Delete Reversibility`, `Import/Export Transfer Tests`, `Group Membership Cache Tests`, `Purge Record Draining`, `List Bin Tests`, `Nested Lookup Tests`, `Orphan Grant Sweep`, `Lookup Grant Tests`, `Reference Column Sorting`, `Ad-hoc Query Filtering`, `Archive Test Fixtures`, `Lookup Option Filters`, `Metric Pushdown Fixtures`, `Permanent Delete Tests`, `Unique Concurrency Tests`, `Rich Text Column Tests`, `Record Archive Tests`, `Metrics Tests`, `Metric Pushdown Tests`, `Dangling Lookup Target Sweep`, `Choice 'Other' Option Tests`, `Form Condition Tests`, `Metric Test Fixtures`, `Record Paging Tests`, `Choice Reference Test`, `Calculated Cast Index Test`, `Location Column Tests`, `View Filtering Tests`, `Phone Column Tests`, `Item Update & Row Lock`, `Conversion Scratch Cleanup`, `Unique + Multiple Guard Tests`, `Test Fixtures & Engine`, `Calculated Reference Tests`, `Cell Patch Tests`, `Text Mode Tests`?**
  _High betweenness centrality (0.232) - this node is a cross-community bridge._
- **Why does `session_factory()` connect `Search & Recompute Locking` to `Conversion Regression Tests`, `List HTTP Flow Tests`, `Record Purge Tests`, `Create Schemas & Grant Upsert`, `Lookup Options & Label Search`, `Purge Maintenance Jobs`, `Soft Delete Reversibility`, `Import/Export Transfer Tests`, `Group Membership Cache Tests`, `List Purge Tests`, `Workspace Purge Tests`, `Purge Record Draining`, `List Clone Tests`, `Reference Column Sorting`, `Orphan Grant Sweep`, `Lookup Grant Tests`, `Calculated Index Corruption`, `Session Config Tests`, `Principal Bin Tests`, `Unique Concurrency Tests`, `List Config Bin Tests`, `Record Archive Tests`, `User Settings Tests`, `Dangling Lookup Target Sweep`, `Account Security Tests`, `List Transfer Tests`, `Login Throttle Tests`, `Partial Reindex Test`, `Workspace Transfer Tests`, `List Config Purge Tests`, `Item Update & Row Lock`, `Conversion Scratch Cleanup`, `Token Revocation Tests`, `Unique + Multiple Guard Tests`, `Calculated Column Tests`, `People CRUD Tests`, `Test Fixtures & Engine`, `Database Session Setup`?**
  _High betweenness centrality (0.125) - this node is a cross-community bridge._
- **Why does `CreatedUser` connect `Batch User Parsing Service` to `Core & Column Schemas`, `Batch User & Import Dialogs`?**
  _High betweenness centrality (0.108) - this node is a cross-community bridge._