-- Where a copy of the bytes lives outside this machine.
--
-- `source_path` and `exports.path` are absolute paths under workspace_root: correct
-- for the editor, worthless to marketing or sales-claw on another host. `object_key`
-- holds the bucket-qualified key of a copy in object storage ("laura/exports/e1.mp4"),
-- or NULL when there is none -- which is the normal state for a desktop install with
-- storage switched off. The local file always stays; the copy is an addition.
ALTER TABLE exports ADD COLUMN object_key TEXT;
ALTER TABLE media_assets ADD COLUMN object_key TEXT;
