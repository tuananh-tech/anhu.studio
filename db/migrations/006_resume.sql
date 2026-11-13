-- 006 — "Ghi nhớ đăng nhập trên thiết bị này": tải lại trang / mở lại ứng dụng trên điện thoại không bắt đăng nhập lại.
-- Trình duyệt giữ một mã ngẫu nhiên (localStorage); DB chỉ lưu SHA-256 của mã, gắn với MỘT phiên. Phiên vẫn hết hạn sau
-- 12 giờ (hoặc 2 giờ không dùng), bị thu hồi khi đăng xuất / khóa tài khoản / quản trị thu hồi / đặt lại mật khẩu.

ALTER TABLE app_sessions ADD COLUMN resume_hash text UNIQUE CHECK (resume_hash IS NULL OR length(resume_hash) = 64);

CREATE OR REPLACE FUNCTION app.session_set_resume(p_session uuid, p_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  -- chỉ đặt được ngay sau khi đăng nhập (phiên mới tạo < 2 phút, chưa có mã)
  UPDATE app_sessions SET resume_hash = p_hash
   WHERE id = p_session AND revoked_at IS NULL AND resume_hash IS NULL AND created_at > now() - interval '2 minutes';
  IF NOT FOUND THEN RAISE EXCEPTION 'SESSION_INVALID' USING ERRCODE = 'P0001'; END IF;
END $$;

CREATE OR REPLACE FUNCTION app.session_resume(p_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE sid uuid;
BEGIN
  SELECT s.id INTO sid FROM app_sessions s JOIN app_users u ON u.id = s.user_id
   WHERE s.resume_hash = p_hash AND s.revoked_at IS NULL AND u.is_active
     AND s.created_at > now() - interval '12 hours' AND s.last_seen_at > now() - interval '2 hours';
  IF sid IS NOT NULL THEN UPDATE app_sessions SET last_seen_at = now() WHERE id = sid; END IF;
  RETURN sid;
END $$;
