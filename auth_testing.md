# Auth Testing Playbook (Emergent Google OAuth)

## Flow
1. Frontend login button redirects to `https://auth.emergentagent.com/?redirect=${encodeURIComponent(window.location.origin + '/')}` — never hardcode the redirect URL.
2. After Google auth, user lands at `{redirect}#session_id={session_id}`.
3. `AuthCallback` detects `location.hash` containing `session_id=` during render (not useEffect) and POSTs it to `/api/auth/session`.
4. Backend calls `GET https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data` with header `X-Session-ID: <session_id>` (backend only, never frontend), stores `session_token` (7-day expiry, timezone-aware) in `user_sessions`, sets httpOnly cookie (`secure=True`, `samesite="none"`, `path="/"`).
5. `/api/auth/me` validates cookie first, then `Authorization: Bearer` fallback.

## Creating a test session without Google
```bash
mongosh --eval "
use('test_database');
var userId = 'test-user-' + Date.now();
var sessionToken = 'test_session_' + Date.now();
db.users.insertOne({
  user_id: userId,
  email: 'test.user.' + Date.now() + '@example.com',
  name: 'Test User',
  picture: 'https://via.placeholder.com/150',
  role: 'analyst',
  created_at: new Date().toISOString()
});
db.user_sessions.insertOne({
  user_id: userId,
  session_token: sessionToken,
  expires_at: new Date(Date.now() + 7*24*60*60*1000).toISOString(),
  created_at: new Date().toISOString()
});
print('Session token: ' + sessionToken);
"
```

## Test backend with the token
```bash
curl -X GET "$API/api/auth/me" -H "Authorization: Bearer <session_token>"
```

## Browser testing
Set cookie `session_token=<token>` (httpOnly, secure, sameSite None) on the app domain, then load the app — dashboard should render, not the login page.

## Owner account
`kunalkavya20@gmail.com` gets role `owner` on first Google login (see OWNER_EMAIL in backend/.env).

## Checklist
- users have custom `user_id` (UUID); all queries project `{"_id": 0}`
- session `user_id` matches user `user_id` exactly
- `/api/auth/me` returns user JSON, not 401
- callback detection uses `useLocation().hash`, not `window.location.hash`
