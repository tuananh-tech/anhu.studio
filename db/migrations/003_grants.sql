-- Quyền của role ứng dụng (tạo bởi scripts/migrate.py). File này được chạy LẠI sau mọi migration
-- để bảng mới luôn có đúng quyền. Luôn viết idempotent.
GRANT USAGE ON SCHEMA public, app TO anhu_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anhu_app;
REVOKE INSERT, UPDATE, DELETE ON audit_log FROM anhu_app;
REVOKE INSERT, DELETE ON app_users FROM anhu_app;
REVOKE ALL ON schema_migrations FROM anhu_app;
GRANT SELECT ON schema_migrations TO anhu_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO anhu_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO anhu_app;

-- Dữ liệu gốc chỉ lưu trữ mềm (archived_at / active / status) — ứng dụng không được xóa cứng.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['students','centers','locations','subjects','class_groups','class_group_members','contacts',
      'rate_rules','timesheet_periods','timesheet_period_versions','timesheet_rule_versions','legacy_imports',
      'reconciliation_imports','backup_runs','session_kinds','teacher_profiles','app_settings','recon_mappings']
  LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('REVOKE DELETE ON %I FROM anhu_app', t);
    END IF;
  END LOOP;
END $$;

-- Bảng tài khoản/phiên/lời mời: ứng dụng chỉ thao tác qua hàm SECURITY DEFINER (005).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['app_meta','account_invites','app_sessions','login_attempts']
  LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON %I FROM anhu_app', t);
    END IF;
  END LOOP;
  -- Hàm nội bộ tạo tài khoản: chỉ được gọi từ bootstrap_admin / accept_invite, không gọi trực tiếp.
  IF to_regprocedure('app._new_account(text,text,text,boolean,jsonb)') IS NOT NULL THEN
    REVOKE EXECUTE ON FUNCTION app._new_account(text, text, text, boolean, jsonb) FROM anhu_app, PUBLIC;
  END IF;
END $$;
