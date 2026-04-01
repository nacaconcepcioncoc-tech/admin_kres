# Manila Timezone Delivery Date Handling

## Overview

The order management system uses **Manila timezone (UTC+8)** consistently for all delivery-related date and time calculations. This ensures that regardless of where the web server is hosted or what timezone the user's browser shows, delivery dates and the "Delivery Tomorrow" feature always use Manila time.

## How It Works

### Backend (Django)

The system provides Manila timezone utilities in `/pages/manila_tz_utils.py`:

- **`get_manila_today()`** - Returns today's date in Manila timezone
- **`get_manila_now()`** - Returns current datetime in Manila timezone  
- **`is_delivery_tomorrow(delivery_date)`** - Checks if a date equals tomorrow in Manila timezone
- **`is_delivery_today(delivery_date)`** - Checks if a date equals today in Manila timezone
- **`get_delivery_date_note(delivery_date)`** - Returns human-readable delivery date label

### Frontend (JavaScript)

The orders page includes a client-side function that calculates Tomorrow's date in Manila timezone:

```javascript
function getManilaDateString(offsetDays = 0) {
    // Create date in Manila timezone (UTC+8)
    const manilaOffset = 8 * 60; // minutes
    const now = new Date();
    const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
    const manilaTime = new Date(utc + (manilaOffset * 60000));
    manilaTime.setDate(manilaTime.getDate() + offsetDays);
    return manilaTime.toISOString().split('T')[0];
}
```

This function:
1. Gets the current UTC time
2. Applies Manila's UTC+8 offset
3. Adds the day offset (0 for today, 1 for tomorrow, etc.)
4. Returns date as 'YYYY-MM-DD' string

### Django Order Model Properties

The `Order` model includes Manila timezone-aware properties:

- **`order.is_delivery_tomorrow`** - Boolean property indicating if delivery is tomorrow in Manila time
- **`order.is_delivery_today`** - Boolean property indicating if delivery is today in Manila time
- **`order.get_delivery_date_note()`** - Returns readable note like "Today", "Tomorrow", or full date

### Template Integration

In templates, you can use:

1. **Template Filters** (requires `{% load manila_tz %}`):
   ```django
   {% if order.delivery_date|is_delivery_tomorrow_filter %}
       <!-- Tomorrow's delivery -->
   {% endif %}
   ```

2. **Order Model Properties**:
   ```django
   {% if order.is_delivery_tomorrow %}
       <span class="badge-tomorrow">TOMORROW</span>
   {% endif %}
   ```

3. **Template Tags**:
   ```django
   {% manila_current_date as manila_today %}
   {% manila_tomorrow_date as manila_tomorrow %}
   ```

## Order Delivery Tomorrow Feature

The "Delivery Tomorrow" notification appears when there are orders scheduled for delivery tomorrow (based on Manila timezone):

- Summary shows: "🚚 There are X deliveries scheduled for tomorrow (Manila time)"
- Click the notification to filter and show only tomorrow's deliveries
- Each order with delivery tomorrow is tagged with a yellow "TOMORROW" badge

## Database Storage

- **`Order.delivery_date`** - Stored as Django `DateField` (date-only, no timezone info)
- **`Order.delivery_time`** - Stored as Django `TimeField` (time-only, no timezone info)
- System calculates "tomorrow" by comparing these dates to the current date in Manila timezone

## Example Scenarios

### Scenario 1: Server in USA, Admin in Manila
- Current time: April 1, 8:00 AM EDT (USA)
- Current time: April 2, 8:00 PM in Manila
- An order with delivery_date=2026-04-02 will show as **"Tomorrow"** and appear in tomorrow's delivery list
- ✅ Correct behavior

### Scenario 2: Server in Singapore, Admin in Manila
- Current time: April 1, 8:00 PM SGT (Singapore, UTC+8)
- Current time: April 1, 8:00 PM in Manila
- An order with delivery_date=2026-04-02 will show as **"Not scheduled for tomorrow"**
- ✅ Correct behavior

### Scenario 3: Edge Case - Just Before Midnight Manila Time
- Current time: April 1, 11:58 PM in Manila
- Current time: April 1, 11:58 AM UTC
- An order with delivery_date=2026-04-02 will show as **"Tomorrow"**
- At 12:00 AM Manila time, the system will automatically show delivery_date=2026-04-02 as **"Today"**
- ✅ Boundary handled correctly

## Settings Configuration

In `storefront/settings.py`:
```python
TIME_ZONE = 'Asia/Manila'
USE_TZ = True
```

This ensures all Django datetime objects use Manila timezone by default.

## Implementation Details

### Why This Matters
1. **Delivery Staff**: Need to know which orders are coming tomorrow to plan routes
2. **Admin Users**: Need accurate delivery scheduling to prevent late or missed deliveries
3. **Consistency**: Whether server is in Manila, USA, or elsewhere, dates are consistent
4. **Correctness**: System calculates based on Manila time, not server system time

### How Updates Work
The "Tomorrow" count updates dynamically:
- Each time the DOM is ready, `updateTomorrowDeliveryCount()` runs
- Compares each order's `data-sort` attribute with `getManilaDateString(1)`
- Shows/hides the delivery summary notification
- Reflects accurate count regardless of server timezone

## Testing

To test the feature:

1. **Check Current Manila Date**:
   - Open browser DevTools (F12)
   - Run: `console.log(getManilaDateString(0))`
   - Compare with actual Manila date

2. **Filter Tomorrow's Deliveries**:
   - Click the "🚚 There are X deliveries scheduled for tomorrow" notification
   - Verify only orders with tomorrow's date show

3. **Verify Badge Display**:
   - Orders with delivery_date = tomorrow should show yellow "TOMORROW" badge
   - Check on different pages throughout the day

## Dependencies

- `pytz` - Python timezone library (installed in requirements.txt)
- Django's `timezone` utilities
- Browser JavaScript Date API

## Files Modified

- `/pages/manila_tz_utils.py` - Core timezone utility functions
- `/pages/views.py` - Order view imports and uses timezone utilities
- `/pages/models.py` - Order model properties for timezone checking
- `/pages/templatetags/manila_tz.py` - Template filters and tags
- `/templates/orders.html` - Display and filtering logic
- `/requirements.txt` - Added pytz dependency

## Troubleshooting

**Issue**: Tomorrow's delivery count not showing
- **Check**: Is there an order with delivery_date = tomorrow in Manila timezone?
- **Check**: Did you click the filter notification? It might have been filtered manually.

**Issue**: "Tomorrow" badge shows on wrong orders
- **Check**: Ensure `delivery_date` field in database contains correct dates
- **Check**: Browser timezone doesn't affect this; it uses Manila timezone calculation

**Issue**: System still showing old timezone
- **Action**: Clear browser cache (Ctrl+Shift+Delete)
- **Action**: Restart Django development server

## Future Enhancements

Possible improvements:
- Add delivery time-based filtering (within specific hours)
- Add "Delivery Today" alerts with different styling
- Create delivery route optimization based on address + delivery date/time
- Add timezone offset visualization in admin dashboard
