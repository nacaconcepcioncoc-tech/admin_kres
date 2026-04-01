# Connect krescoadmin.xyz Domain to Your Render Application

## Overview
You have:
- ✅ Domain: krescoadmin.xyz (from Namecheap)
- ✅ App Hosting: Render.com
- ✅ Django Application: KRES Admin

This guide will connect them together in 3 main steps.

---

## STEP 1: Update and Deploy Code to Render (Already Done ✅)

The configuration has been updated in `render.yaml` to allow your custom domain:
- ALLOWED_HOSTS: ".onrender.com,krescoadmin.xyz,www.krescoadmin.xyz"
- CSRF_TRUSTED_ORIGINS: "https://*.onrender.com,https://krescoadmin.xyz,https://www.krescoadmin.xyz"

**You just need to push to GitHub:**

```bash
git add render.yaml
git commit -m "config: Add krescoadmin.xyz to ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS"
git push origin main
```

This will automatically trigger Render to redeploy your application with the new settings.

---

## STEP 2: Add Custom Domain in Render Dashboard

### 2.1 Go to Render Dashboard
1. Go to https://dashboard.render.com
2. Click on your service: **"kres-admin"**
3. Click on the **"Settings"** tab

### 2.2 Add Custom Domain
1. Scroll to **"Custom Domains"** section
2. Click **"Add Custom Domain"**
3. Enter: **krescoadmin.xyz**
4. Click **"Add"**
5. You'll see a panel showing your domain and status as **"Awaiting DNS Setup"** (this is normal)

### 2.3 Get Render's DNS Information
Render will show you two options:
- **Option A: Use CNAME record (Recommended)**
- **Option B: Use A record**

For most cases, use **CNAME** (which is what I'll guide you for).

You should see something like:
```
Type: CNAME
Name: krescoadmin.xyz
Value: [something].onrender.com
```

**Copy this value** - you'll need it for Namecheap.

---

## STEP 3: Configure DNS on Namecheap

### 3.1 Go to Namecheap Dashboard
1. Go to https://www.namecheap.com
2. Log in to your account
3. Click **"Domain List"** in left sidebar
4. Find **krescoadmin.xyz**
5. Click **"Manage"**

### 3.2 Go to DNS Settings
1. Click **"Advanced DNS"** tab
2. Scroll down to **"Host Records"** section

### 3.3 Add CNAME Record for Root Domain
This allows `krescoadmin.xyz` to point to Render.

**Add one new record:**

| Name | Type | Value | TTL |
|------|------|-------|-----|
| @ | CNAME | [Your Render hostname from Step 2.3] | 30 min |

Example:
- Name: `@`
- Type: `CNAME`
- Value: `kres-admin.onrender.com` (from Render)
- TTL: 30 min

**Click Save**

### 3.4 Add CNAME Record for www Subdomain (Optional but Recommended)
This allows `www.krescoadmin.xyz` to also work.

**Add another record:**

| Name | Type | Value | TTL |
|------|------|-------|-----|
| www | CNAME | [Same Render hostname] | 30 min |

Example:
- Name: `www`
- Type: `CNAME`
- Value: `kres-admin.onrender.com`
- TTL: 30 min

**Click Save**

---

## STEP 4: Wait for DNS Propagation

DNS changes can take **5 minutes to 48 hours** to fully propagate, but usually it's faster.

### Check DNS Status:
1. Return to Render Dashboard > Your Service > Settings > Custom Domains
2. Your domain should change from **"Awaiting DNS Setup"** to **"Verified"**
3. This confirms the DNS is properly configured

### Verify It Works:
1. Open your browser and go to: `https://krescoadmin.xyz`
2. If it shows your admin dashboard → **Success! 🎉**
3. Also try: `https://www.krescoadmin.xyz` (should also work)

---

## Troubleshooting

### Issue: Domain shows "Awaiting DNS Setup" after 1 hour

**Solution:**
1. Verify your Namecheap DNS records are correct
2. Check that you used the **exact value** Render gave you
3. Clear browser cache (Ctrl+Shift+Delete) and try again
4. Use a DNS checker: https://dnschecker.org/
5. Enter krescoadmin.xyz and check if CNAME points to Render

### Issue: Gets "Invalid HTTP_HOST header" error

**Solution:**
- Django settings weren't updated properly
- Go to Render Dashboard > Settings > Environment
- Verify ALLOWED_HOSTS contains all of:
  ```
  .onrender.com,krescoadmin.xyz,www.krescoadmin.xyz
  ```
- Trigger a redeploy (go to Deployments > Retrigger)

### Issue: HTTPS shows certificate warning

**Solution:**
- Render automatically provides SSL for custom domains
- Verify domain shows "Verified" in Render Settings
- Wait a few minutes then refresh browser
- Try in incognito mode to clear cache

### Issue: Namecheap DNS isn't resolving

**Solution:**
1. Make sure you're in the correct domain
2. Verify you're in "Advanced DNS" section
3. Check the exact Render hostname (no typos)
4. Some TTLs take longer - try waiting 5-10 minutes
5. Use `nslookup` to test:
   ```bash
   nslookup krescoadmin.xyz
   ```

---

## Quick Summary

| Step | Action | Time |
|------|--------|------|
| 1 | Push code to GitHub | 2 min |
| 2 | Add domain in Render | 2 min |
| 3 | Configure DNS on Namecheap | 5 min |
| 4 | Wait for DNS propagation | 5-48 hours |
| **Total** | | **Up to 48 hours** |

Most of the time it's active within **15-30 minutes**!

---

## After Domain is Live

Once `krescoadmin.xyz` is working:

### Update Any Reference Links
If you have:
- Email confirmations
- Documentation
- API endpoints
- Frontend configs

Update them to use: `https://krescoadmin.xyz` instead of the Render URL.

### Enable Automatic Redirects (Optional)
Add to Django settings to redirect http → https:
```python
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

### Monitor Your Domain
Keep an eye on your Render dashboard to ensure your service stays up and healthy.

---

## Support Links

- **Render Documentation:** https://docs.render.com/custom-domains
- **Namecheap DNS Help:** https://www.namecheap.com/support/knowledgebase/
- **Django Allowed Hosts:** https://docs.djangoproject.com/en/6.0/ref/settings/#allowed-hosts
- **DNS Checker:** https://dnschecker.org/

---

## Next Steps

1. **Update render.yaml** - Configure domain settings
2. **Push to GitHub** - Trigger Render deployment
3. **Add domain in Render** - Set up custom domain
4. **Configure DNS** - Point Namecheap to Render
5. **Test access** - Verify krescoadmin.xyz works
6. **Update documentation** - Point users to new domain

Good luck! 🚀
