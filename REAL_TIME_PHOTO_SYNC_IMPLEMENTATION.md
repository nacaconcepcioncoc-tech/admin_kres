# Real-Time Photo Sync Implementation - Complete Documentation

## Overview
Implemented real-time image synchronization for order photos across multiple admin devices. Photos uploaded on one device automatically appear on all other devices within ~3 seconds via polling mechanism.

---

## Architecture

### Frontend (orders.html)
**Polling-based architecture** with client-side hash comparison for change detection.

#### Key Components:

1. **Photo Upload** (`handlePhotoUpload()`)
   - Shows immediate local preview via FileReader API
   - Sends FormData to `/upload-order-photo/` with CSRF protection
   - Calls `startImageSyncPolling()` after successful upload
   - Stores file in `request.FILES['photo']` with multipart/form-data encoding

2. **Real-time Sync Polling** (`startImageSyncPolling()`)
   - Polls `/get-order-photo/?order_id={orderId}` every 3 seconds
   - Compares returned `photo_hash` with previous hash (`lastImageHash`)
   - If hash differs, updates image with cache-busting: `src = url + '?t=' + Date.now()`
   - Eliminates unnecessary re-renders when file unchanged

3. **Modal Lifecycle Management**
   - `viewOrderDetails()`: Fetches photo from server and starts polling
   - Falls back to localStorage if server endpoints unavailable
   - `closeOrderDetailsModal()`: Stops polling via `stopImageSyncPolling()`
   - Ensures cleanup when modal closed to prevent memory leaks

4. **Photo Deletion** (`removePhoto()`)
   - Calls `/delete-order-photo/` to remove photos from server
   - Sends JSON: `{ order_id: orderId }`
   - Clears local UI after deletion

#### State Management:
```javascript
window._currentOrderId = null;          // Track currently viewed order
imageSyncPollingInterval = null;        // Polling interval ID for cleanup
lastImageHash = null;                   // Track previous hash for change detection
tomorrowFilterActive = false;           // Delivery filter toggle state
```

---

### Backend (Django Views)

#### 1. **POST `/upload-order-photo/`** - `upload_order_photo()`

**Purpose**: Receive photo upload, store on server, return hash for polling

**Receives**:
- `order_id` (POST parameter)
- `photo` (file upload via FileReader → FormData)

**Returns**:
```json
{
  "success": true,
  "photo_url": "/media/order_photos/123/photo_1706123456789.jpg",
  "photo_hash": "a1b2c3d4e5f6g7h8i9j0",
  "photo_name": "photo.jpg"
}
```

**Implementation Details**:
- Validates order exists in database
- Creates directory: `media/order_photos/{order_id}/`
- Deletes existing photos (keeps only latest)
- Saves with timestamp filename: `photo_1706123456789.jpg`
- Generates MD5 hash for change detection
- CSRF protected with `@csrf_protect`

#### 2. **GET `/get-order-photo/?order_id={id}`** - `get_order_photo()`

**Purpose**: Retrieve latest photo + hash for polling mechanism

**Receives**: `order_id` (query parameter)

**Returns**:
```json
{
  "success": true,
  "photo_url": "/media/order_photos/123/photo_1706123456789.jpg",
  "photo_hash": "a1b2c3d4e5f6g7h8i9j0",
  "photo_name": "photo_1706123456789.jpg"
}
```

Or if no photo:
```json
{
  "success": true,
  "photo_url": null,
  "photo_hash": null,
  "photo_name": null
}
```

**Implementation Details**:
- Checks `media/order_photos/{order_id}/` directory
- Gets most recent file by modification time
- Calculates MD5 hash from file content
- Returns photo_url, photo_hash, photo_name
- No authentication required (read-only, but could add @login_required)

#### 3. **POST `/delete-order-photo/`** - `delete_order_photo()`

**Purpose**: Delete all photos for an order

**Receives**: JSON body `{ order_id: "123" }`

**Returns**:
```json
{
  "success": true
}
```

**Implementation Details**:
- Validates order exists
- Removes all files in `media/order_photos/{order_id}/`
- Removes empty directory
- CSRF protected with `@csrf_protect`
- Requires login via `@login_required`

---

## File Storage Structure

```
media/
└── order_photos/
    ├── Order-123-2024/
    │   └── photo_1706123456789.jpg
    ├── Order-124-2024/
    │   └── photo_1706123457890.jpg
    └── Order-125-2024/
        └── photo_1706123458901.jpg
```

**Filename Pattern**: `photo_{timestamp_ms}.{extension}`
**Timestamp**: Milliseconds since epoch (ensures unique names)

---

## Configuration Files Modified

### 1. **pages/views.py**
- Added 4 new helper functions:
  - `generate_photo_hash()` - MD5 hash generation
  - `get_order_photo_path()` - Directory path helper
  - `get_order_photo_url()` - URL generation helper
- Added 3 API endpoints:
  - `upload_order_photo()` - POST /upload-order-photo/
  - `get_order_photo()` - GET /get-order-photo/
  - `delete_order_photo()` - POST /delete-order-photo/
- Imports: `hashlib`, `os.path`, `django.conf.settings`

### 2. **pages/urls.py**
- Added URL routes:
  ```python
  path('upload-order-photo/', views.upload_order_photo, name='upload_order_photo'),
  path('get-order-photo/', views.get_order_photo, name='get_order_photo'),
  path('delete-order-photo/', views.delete_order_photo, name='delete_order_photo'),
  ```

### 3. **storefront/settings.py**
- Added media file configuration:
  ```python
  MEDIA_URL = '/media/'
  MEDIA_ROOT = BASE_DIR / 'media'
  ```

### 4. **storefront/urls.py**
- Added media file serving for development:
  ```python
  from django.conf.urls.static import static
  
  if settings.DEBUG:
      urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
  ```

### 5. **templates/orders.html**
- **Updated functions**:
  - `handlePhotoUpload()` - Now uploads to `/upload-order-photo/`
  - `removePhoto()` - Now deletes via `/delete-order-photo/`
  - `viewOrderDetails()` - Fetches photo from server, starts polling
  - `closeOrderDetailsModal()` - Stops polling on close
- **New functions**:
  - `startImageSyncPolling()` - 3-second polling with hash comparison
  - `stopImageSyncPolling()` - Cleanup function
- **Delivery filter**:
  - `filterTomorrowDeliveries()` - Toggle filter state and update display

---

## Real-Time Sync Flow

### Upload Flow:
```
Device A: Click upload
   ↓
handlePhotoUpload() - FileReader preview
   ↓
POST /upload-order-photo/ with FormData
   ↓
Backend saves file, generates hash
   ↓
Returns: photo_url, photo_hash
   ↓
startImageSyncPolling(orderId) on Device A
   ↓
startImageSyncPolling(orderId) on Device B, C, etc.
```

### Polling Flow (Every 3 seconds):
```
GET /get-order-photo/?order_id=ORDER-123-2024
   ↓
Backend returns: photo_url, photo_hash
   ↓
Client compares: photo_hash !== lastImageHash?
   ↓
If different:
   - Update: img.src = photo_url + '?t=' + Date.now()
   - Update: lastImageHash = photo_hash
   ↓
Repeat every 3000ms
```

### Deletion Flow:
```
Device A: Click delete
   ↓
removePhoto() confirmation
   ↓
POST /delete-order-photo/ with { order_id }
   ↓
Backend removes: media/order_photos/{order_id}/*
   ↓
Client clears: img.src = '', display = 'none'
   ↓
Other devices polling: /get-order-photo/ returns null
   ↓
Clears image on poll
```

---

## Delivery Filter Enhancement

### Feature: Clickable Delivery Summary
- Summary div shows: "🚚 There are 2 deliveries scheduled for tomorrow"
- Clicking filters table to show only tomorrow's deliveries
- Filter indicator shows: "FILTERED" with visible count
- `tomorrowFilterActive` boolean tracks state
- Calculated date: `tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1)`

### Table Sorting
- Rows sorted by delivery date (YYYY-MM-DD format)
- Closest delivery date appears first
- Uses `data-sort` attribute for reliable date comparison

---

## Performance Optimizations

1. **Hash-based Change Detection**
   - MD5 hash comparison prevents unnecessary image re-renders
   - Polling only updates DOM when file actually changed
   - 3-second polling interval balances responsiveness vs. server load

2. **Cache Busting**
   - Query string timestamp: `src = url + '?t=' + Date.now()`
   - Forces browser to fetch latest version from server
   - Prevents stale browser cache issues

3. **File Management**
   - Only latest photo kept (old ones auto-deleted on upload)
   - Reduces storage bloat
   - Most recent file determined by modification time

4. **Error Handling**
   - Graceful fallback to localStorage if endpoints unavailable
   - Continues polling even if initial fetch fails
   - Try/catch blocks for file operations

---

## Security Considerations

1. **CSRF Protection**
   - `@csrf_protect` on upload/delete endpoints
   - CSRF token included in fetch headers
   - Frontend retrieves token: `document.querySelector('[name=csrfmiddlewaretoken]')?.value`

2. **Authentication**
   - `@login_required` on upload/delete endpoints
   - GET endpoint (get-order-photo) could be protected but kept open for polling
   - Currently: Anyone who knows order_id can view photos

3. **Order Validation**
   - All endpoints verify order exists before processing
   - Prevents access to non-existent order photo directories

4. **File Type Validation**
   - Consider adding file type validation (is_image check)
   - Set maximum file size limit
   - Implement in future iteration if needed

---

## Testing Checklist

- [ ] Upload photo on Device A
- [ ] Open order on Device B within 3 seconds
- [ ] Verify photo appears on Device B
- [ ] Modify photo filename on server
- [ ] Poll next cycle (~3 seconds) should detect change
- [ ] Delete photo on Device A
- [ ] Device B polling should detect deletion within 3 seconds
- [ ] Modal closes, polling stops (check browser console for no errors)
- [ ] Fallback to localStorage works if endpoints missing

---

## Backward Compatibility

- **localStorage Fallback**: If backend endpoints unavailable, system falls back to localStorage
- **Graceful Degradation**: Photo still displays from localStorage if server returns null
- **No Breaking Changes**: Existing order functionality unaffected

---

## Future Enhancements

1. **WebSocket for Real-time Updates**
   - Replace polling with WebSocket for < 1 second updates
   - Reduces server load significantly

2. **Multiple Photo Types**
   - Support Before/After photos
   - Support invoice/receipt photos
   - Support customer signature photos

3. **Photo Cleanup Job**
   - Scheduled task to delete photos for completed orders after N days
   - Implement with `celery` or Django Management Commands

4. **Advanced Search**
   - Search photos by order date, customer, status
   - Use elasticsearch for large deployments

5. **Photo Compression**
   - Compress images on upload to reduce storage
   - Generate thumbnails for preview

---

## Endpoints Summary

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/upload-order-photo/` | ✓ | Upload new photo, return hash |
| GET | `/get-order-photo/` | ✗ | Retrieve photo for polling |
| POST | `/delete-order-photo/` | ✓ | Delete order photos |

**✓** = Requires login  
**✗** = Public read access (consider adding auth)

---

## Troubleshooting

### Photo not appearing on other devices
1. Check browser console for fetch errors
2. Verify `/get-order-photo/` returns correct photo_url
3. Check media/ directory permissions
4. Ensure MEDIA_ROOT and MEDIA_URL configured correctly

### Hash always different (not detecting changes)
1. Verify MD5 hash is calculated from file content, not metadata
2. Check file is actually being saved to disk
3. Inspect returned photo_hash vs. calculated hash

### Photos deleted but clients still see them
1. Ensure polling continues after deletion
2. Verify `/delete-order-photo/` removes directory completely
3. Check browser cache-busting working (timestamp added to URL)

---

## Files Changed

1. `pages/views.py` - Added 3 API endpoints + helpers
2. `pages/urls.py` - Added 3 URL routes
3. `storefront/settings.py` - Added MEDIA_URL, MEDIA_ROOT
4. `storefront/urls.py` - Added media file serving
5. `templates/orders.html` - Updated JavaScript for polling

