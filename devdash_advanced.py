from datetime import datetime, timedelta
from decimal import Decimal
import json
from aiohttp import web
from devdash_api import _auth_user, _json, fetch, fetchrow, execute

# ============================================================================
# AD PERFORMANCE ANALYTICS
# ============================================================================

async def ad_performance_report(request):
    """Generiere detaillierten Ad-Performance Report"""
    await _auth_user(request)
    ad_id = request.query.get("ad_id")
    days = int(request.query.get("days", "30"))
    
    sql = """
        SELECT 
            ad_id,
            event_type,
            COUNT(*) as count,
            COUNT(DISTINCT telegram_id) as unique_users,
            COUNT(DISTINCT bot_username) as bot_count
        FROM dashboard_ad_events
        WHERE created_at > now() - interval '%s days'
    """
    params = (days,)
    
    if ad_id:
        sql += " AND ad_id = %s"
        params = params + (ad_id,)
    
    sql += " GROUP BY ad_id, event_type ORDER BY ad_id"
    
    rows = await fetch(sql, params)
    
    # Calculate CTR (Click-Through Rate)
    data = {}
    for row in rows:
        aid = row['ad_id']
        if aid not in data:
            data[aid] = {'impressions': 0, 'clicks': 0}
        
        if row['event_type'] == 'impression':
            data[aid]['impressions'] = row['count']
        elif row['event_type'] == 'click':
            data[aid]['clicks'] = row['count']
    
    # Add CTR
    for aid in data:
        impressions = data[aid]['impressions']
        clicks = data[aid]['clicks']
        data[aid]['ctr'] = (clicks / impressions * 100) if impressions > 0 else 0
    
    return _json({"report": data}, request)


async def ad_roi_analysis(request):
    """Berechne ROI für Ad-Kampagnen"""
    await _auth_user(request)
    
    sql = """
        SELECT 
            da.id,
            da.name,
            da.placement,
            da.bot_slug,
            COUNT(CASE WHEN dae.event_type = 'impression' THEN 1 END) as impressions,
            COUNT(CASE WHEN dae.event_type = 'click' THEN 1 END) as clicks,
            COUNT(CASE WHEN dae.event_type = 'view' THEN 1 END) as views,
            COUNT(DISTINCT dae.telegram_id) as unique_users,
            da.created_at,
            da.updated_at
        FROM dashboard_ads da
        LEFT JOIN dashboard_ad_events dae ON da.id = dae.ad_id
        WHERE da.created_at > now() - interval '90 days'
        GROUP BY da.id, da.name, da.placement, da.bot_slug, da.created_at, da.updated_at
        ORDER BY clicks DESC
    """
    
    rows = await fetch(sql)
    
    return _json({
        "campaigns": rows,
        "total_impressions": sum(r['impressions'] or 0 for r in rows),
        "total_clicks": sum(r['clicks'] or 0 for r in rows),
        "avg_ctr": sum((r['clicks'] or 0) / (r['impressions'] or 1) * 100 for r in rows) / len(rows) if rows else 0
    }, request)


# ============================================================================
# USER SEGMENTATION & ANALYSIS
# ============================================================================

async def user_segments_analysis(request):
    """Analysiere Benutzer nach Segmenten"""
    await _auth_user(request)
    
    sql = """
        SELECT 
            tier,
            role,
            COUNT(*) as user_count,
            COUNT(CASE WHEN near_account_id IS NOT NULL THEN 1 END) as near_connected,
            COUNT(CASE WHEN ton_address IS NOT NULL THEN 1 END) as ton_connected,
            MIN(created_at) as first_user,
            MAX(created_at) as last_user
        FROM dashboard_users
        GROUP BY tier, role
        ORDER BY user_count DESC
    """
    
    rows = await fetch(sql)
    
    return _json({
        "segments": rows,
        "total_users": sum(r['user_count'] for r in rows),
        "connected_near": sum(r['near_connected'] for r in rows),
        "connected_ton": sum(r['ton_connected'] for r in rows)
    }, request)


async def user_retention_analysis(request):
    """Berechne User Retention Rate"""
    await _auth_user(request)
    days = int(request.query.get("days", "30"))
    
    # Users created in this period
    sql_new = """
        SELECT COUNT(*) as new_users 
        FROM dashboard_users 
        WHERE created_at > now() - interval '%s days'
    """
    
    # Users active in last 7 days
    sql_active = """
        SELECT COUNT(*) as active_users
        FROM dashboard_users
        WHERE updated_at > now() - interval '7 days'
    """
    
    new_users_row = await fetchrow(sql_new, (days,))
    active_users_row = await fetchrow(sql_active)
    
    new_users = new_users_row['new_users'] if new_users_row else 0
    active_users = active_users_row['active_users'] if active_users_row else 0
    
    retention_rate = (active_users / new_users * 100) if new_users > 0 else 0
    
    return _json({
        "new_users": new_users,
        "active_users": active_users,
        "retention_rate": retention_rate,
        "period_days": days
    }, request)


# ============================================================================
# TOKEN ECONOMICS ANALYSIS
# ============================================================================

async def token_economics_summary(request):
    """Zusammenfassung der Token-Ökonomie"""
    await _auth_user(request)
    days = int(request.query.get("days", "30"))
    
    sql = """
        SELECT 
            kind,
            COUNT(*) as transaction_count,
            SUM(CAST(amount AS numeric)) as total_amount,
            AVG(CAST(amount AS numeric)) as avg_amount,
            MIN(CAST(amount AS numeric)) as min_amount,
            MAX(CAST(amount AS numeric)) as max_amount
        FROM dashboard_token_events
        WHERE happened_at > now() - interval '%s days'
        GROUP BY kind
        ORDER BY total_amount DESC
    """
    
    rows = await fetch(sql, (days,))
    
    # Calculate totals
    total_minted = sum(float(r['total_amount'] or 0) for r in rows if r['kind'] == 'mint')
    total_burned = sum(float(r['total_amount'] or 0) for r in rows if r['kind'] == 'burn')
    
    return _json({
        "summary": rows,
        "total_minted": total_minted,
        "total_burned": total_burned,
        "net_supply_change": total_minted - total_burned,
        "period_days": days
    }, request)


async def token_velocity_analysis(request):
    """Analysiere Token Velocity"""
    await _auth_user(request)
    
    sql = """
        SELECT 
            DATE_TRUNC('day', happened_at)::DATE as day,
            kind,
            SUM(CAST(amount AS numeric)) as daily_volume,
            COUNT(*) as transaction_count
        FROM dashboard_token_events
        WHERE happened_at > now() - interval '90 days'
        GROUP BY day, kind
        ORDER BY day DESC
    """
    
    rows = await fetch(sql)
    
    return _json({
        "velocity": rows,
        "chart_data": {
            "dates": list(set(r['day'] for r in rows)),
            "volumes": [r['daily_volume'] for r in rows]
        }
    }, request)


# ============================================================================
# BOT HEALTH & PERFORMANCE
# ============================================================================

async def bot_health_dashboard(request):
    """Umfassender Bot Health Check"""
    await _auth_user(request)
    
    sql = """
        SELECT 
            db.username,
            db.title,
            db.is_active,
            COUNT(dbe.id) as endpoint_count,
            COUNT(CASE WHEN dbe.last_seen > now() - interval '5 minutes' THEN 1 END) as healthy_endpoints,
            MAX(dbe.last_seen) as last_health_check,
            json_object_agg(dbe.base_url, dbe.health_path) as endpoints
        FROM dashboard_bots db
        LEFT JOIN dashboard_bot_endpoints dbe ON db.username = dbe.bot_username
        GROUP BY db.username, db.title, db.is_active
    """
    
    rows = await fetch(sql)
    
    # Calculate health scores
    for row in rows:
        endpoints = row['endpoint_count'] or 0
        healthy = row['healthy_endpoints'] or 0
        row['health_score'] = (healthy / endpoints * 100) if endpoints > 0 else 0
        row['status'] = 'healthy' if row['health_score'] >= 80 else 'warning' if row['health_score'] >= 50 else 'critical'
    
    return _json({
        "bots": rows,
        "overall_health": sum(r['health_score'] for r in rows) / len(rows) if rows else 0
    }, request)


# ============================================================================
# WEBHOOK SYSTEM
# ============================================================================

async def webhook_create(request):
    """Erstelle einen Webhook"""
    await _auth_user(request)
    body = await request.json()
    
    event_type = (body.get("event_type") or "").strip()
    url = (body.get("url") or "").strip()
    secret = (body.get("secret") or "").strip()
    is_active = bool(body.get("is_active", True))
    
    if not event_type or not url:
        return _json({"error": "event_type und url erforderlich"}, request, status=400)
    
    await execute("""
        create table if not exists dashboard_webhooks (
            id bigserial primary key,
            event_type text not null,
            url text not null,
            secret text,
            is_active boolean default true,
            last_triggered timestamptz,
            failure_count integer default 0,
            created_at timestamptz default now()
        );
    """)
    
    await execute("""
        insert into dashboard_webhooks(event_type, url, secret, is_active)
        values (%s, %s, %s, %s)
    """, (event_type, url, secret, is_active))
    
    return _json({"ok": True}, request, status=201)


async def webhook_test(request):
    """Test einen Webhook"""
    await _auth_user(request)
    webhook_id = request.match_info.get("id")
    
    webhook = await fetchrow("select * from dashboard_webhooks where id = %s", (webhook_id,))
    
    if not webhook:
        return _json({"error": "Webhook nicht gefunden"}, request, status=404)
    
    # Send test payload
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                webhook['url'],
                json={"test": True, "timestamp": datetime.utcnow().isoformat()},
                headers={"X-Webhook-Secret": webhook['secret'] or ""}
            )
        return _json({"ok": True, "status": "delivered"}, request)
    except Exception as e:
        return _json({"ok": False, "error": str(e)}, request, status=400)


# ============================================================================
# EXPORT & REPORTS
# ============================================================================

async def export_users_csv(request):
    """Exportiere Benutzerliste als CSV"""
    await _auth_user(request)
    
    rows = await fetch("""
        select telegram_id, username, first_name, last_name, role, tier, created_at
        from dashboard_users
        order by created_at desc
    """)
    
    # CSV Header
    csv = "telegram_id,username,first_name,last_name,role,tier,created_at\n"
    
    # CSV Rows
    for row in rows:
        csv += f"{row['telegram_id']},{row['username']},{row['first_name']},{row['last_name']},{row['role']},{row['tier']},{row['created_at']}\n"
    
    return web.Response(
        text=csv,
        content_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="users.csv"'}
    )


async def export_ads_report(request):
    """Exportiere Ad-Performance Report"""
    await _auth_user(request)
    
    sql = """
        select 
            da.name,
            da.placement,
            da.bot_slug,
            count(case when dae.event_type = 'impression' then 1 end) as impressions,
            count(case when dae.event_type = 'click' then 1 end) as clicks,
            count(distinct dae.telegram_id) as unique_users
        from dashboard_ads da
        left join dashboard_ad_events dae on da.id = dae.ad_id
        group by da.name, da.placement, da.bot_slug
        order by clicks desc
    """
    
    rows = await fetch(sql)
    
    # JSON Export
    return _json({
        "report": rows,
        "generated_at": datetime.utcnow().isoformat(),
        "total_ads": len(rows)
    }, request)


# ============================================================================
# INTEGRATION GUIDE
# ============================================================================
"""
Um diese Advanced Features zu verwenden, füge folgende Imports in devdash_api.py hinzu:

from devdash_advanced import (
    ad_performance_report,
    ad_roi_analysis,
    user_segments_analysis,
    user_retention_analysis,
    token_economics_summary,
    token_velocity_analysis,
    bot_health_dashboard,
    webhook_create,
    webhook_test,
    export_users_csv,
    export_ads_report
)

Dann registriere die Routes in register_devdash_routes():

    # Advanced Analytics
    app.router.add_route("GET", "/devdash/analytics/ads/performance", ad_performance_report)
    app.router.add_route("GET", "/devdash/analytics/ads/roi", ad_roi_analysis)
    app.router.add_route("GET", "/devdash/analytics/users/segments", user_segments_analysis)
    app.router.add_route("GET", "/devdash/analytics/users/retention", user_retention_analysis)
    app.router.add_route("GET", "/devdash/analytics/token/economics", token_economics_summary)
    app.router.add_route("GET", "/devdash/analytics/token/velocity", token_velocity_analysis)
    app.router.add_route("GET", "/devdash/analytics/bots/health", bot_health_dashboard)
    
    # Webhooks
    app.router.add_post("/devdash/webhooks", webhook_create)
    app.router.add_post("/devdash/webhooks/{id}/test", webhook_test)
    
    # Exports
    app.router.add_route("GET", "/devdash/export/users.csv", export_users_csv)
    app.router.add_route("GET", "/devdash/export/ads-report", export_ads_report)
"""
