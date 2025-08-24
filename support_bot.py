import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
import os
from urllib.parse import urlparse

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, 
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

class SupportBot:
    def __init__(self, token: str, main_admin_id: int, admin_group_id: int):
        self.token = token
        self.main_admin_id = main_admin_id
        self.admin_group_id = admin_group_id
        self.database_url = os.getenv("DATABASE_URL")
        self.init_database()
        
    def get_db_connection(self):
        """Get database connection - PostgreSQL or SQLite fallback"""
        if self.database_url and self.database_url.startswith('postgresql'):
            # PostgreSQL connection
            print(f"🐘 Connecting to PostgreSQL...")
            return psycopg2.connect(self.database_url, cursor_factory=RealDictCursor)
        else:
            # SQLite fallback
            print(f"🗄️ Falling back to SQLite (DATABASE_URL: {self.database_url})")
            return sqlite3.connect('support_tickets.db')
        
    def init_database(self):
        """Initialize database - PostgreSQL or SQLite"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        if self.database_url:
            # PostgreSQL table creation
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tickets (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'open',
                    priority TEXT DEFAULT 'normal',
                    assigned_admin BIGINT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ticket_messages (
                    id SERIAL PRIMARY KEY,
                    ticket_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    message TEXT,
                    message_type TEXT DEFAULT 'text',
                    file_id TEXT,
                    is_admin BOOLEAN DEFAULT FALSE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    role TEXT DEFAULT 'admin',
                    added_by BIGINT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else:
            # SQLite table creation (fallback)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'open',
                    priority TEXT DEFAULT 'normal',
                    assigned_admin INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ticket_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    message TEXT,
                    message_type TEXT DEFAULT 'text',
                    file_id TEXT,
                    is_admin BOOLEAN DEFAULT FALSE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    role TEXT DEFAULT 'admin',
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        # Insert default categories
        default_categories = [
            ('General Question', 'General questions and inquiries'),
            ('Bug Report', 'Report bugs and technical issues'),
            ('Partnership', 'Partnership and collaboration requests')
        ]
        
        if self.database_url:
            # PostgreSQL syntax
            cursor.executemany('''
                INSERT INTO categories (name, description) VALUES (%s, %s)
                ON CONFLICT (name) DO NOTHING
            ''', default_categories)
            
            # Insert main admin
            cursor.execute('''
                INSERT INTO admins (user_id, username, role, added_by) 
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO NOTHING
            ''', (self.main_admin_id, 'Main Admin', 'main_admin', self.main_admin_id))
        else:
            # SQLite syntax
            cursor.executemany('''
                INSERT OR IGNORE INTO categories (name, description) VALUES (?, ?)
            ''', default_categories)
            
            cursor.execute('''
                INSERT OR IGNORE INTO admins (user_id, username, role, added_by) 
                VALUES (?, ?, 'main_admin', ?)
            ''', (self.main_admin_id, 'Main Admin', self.main_admin_id))
        
        conn.commit()
        conn.close()
        
        # Log database type
        db_type = "PostgreSQL" if self.database_url else "SQLite"
        print(f"🗄️ Database initialized: {db_type}")

    def execute_query(self, query: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False):
        """Execute database query with proper parameter binding"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Convert SQLite ? to PostgreSQL %s if needed
            if self.database_url and '?' in query:
                query = query.replace('?', '%s')
            
            cursor.execute(query, params)
            
            if fetch_one:
                result = cursor.fetchone()
                if self.database_url and result:
                    # Convert RealDictRow to tuple for compatibility
                    result = tuple(result.values())
                return result
            elif fetch_all:
                results = cursor.fetchall()
                if self.database_url and results:
                    # Convert RealDictRow to tuples for compatibility
                    results = [tuple(row.values()) for row in results]
                return results
            else:
                conn.commit()
                return cursor.lastrowid if hasattr(cursor, 'lastrowid') else cursor.rowcount
        finally:
            conn.close()

    def get_categories(self) -> List[tuple]:
        """Get all available categories"""
        return self.execute_query('SELECT name, description FROM categories ORDER BY name', fetch_all=True)

    def get_ticket(self, ticket_id: int) -> Optional[tuple]:
        """Get ticket details"""
        return self.execute_query('''
            SELECT id, user_id, username, category, subject, description, 
                   status, assigned_admin, created_at, updated_at
            FROM tickets WHERE id = ?
        ''', (ticket_id,), fetch_one=True)

    def get_ticket_messages(self, ticket_id: int) -> List[tuple]:
        """Get all messages for a ticket"""
        return self.execute_query('''
            SELECT user_id, username, message, message_type, file_id, 
                   is_admin, timestamp
            FROM ticket_messages 
            WHERE ticket_id = ? 
            ORDER BY timestamp ASC
        ''', (ticket_id,), fetch_all=True)

    def is_admin(self, user_id: int) -> bool:
        """Check if user is an admin"""
        result = self.execute_query('SELECT role FROM admins WHERE user_id = ?', (user_id,), fetch_one=True)
        return result is not None

    def is_main_admin(self, user_id: int) -> bool:
        """Check if user is the main admin"""
        return user_id == self.main_admin_id

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        welcome_text = f"👋 Welcome to Support, {user.first_name}!\n\n"
        welcome_text += "I'm here to help you with any questions or issues you might have.\n\n"
        welcome_text += "🎫 To create a new support ticket, use /ticket\n"
        welcome_text += "📋 To view your tickets, use /mytickets\n\n"
        
        if self.is_admin(user.id):
            welcome_text += "🔧 **Admin Commands:**\n"
            welcome_text += "/dashboard - View all tickets\n"
            welcome_text += "/stats - View statistics\n"
            welcome_text += "/menu - Admin control panel\n"
        
        await update.message.reply_text(welcome_text)

    async def create_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start ticket creation process"""
        categories = self.get_categories()
        
        if not categories:
            await update.message.reply_text("❌ No categories available. Please contact an administrator.")
            return
        
        keyboard = []
        for category_name, _ in categories:
            keyboard.append([InlineKeyboardButton(category_name, callback_data=f"cat_{category_name}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🎫 **Create New Ticket**\n\n"
            "Please select a category for your ticket:",
            reply_markup=reply_markup
        )

    async def category_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle category selection"""
        query = update.callback_query
        await query.answer()
        
        category = query.data.replace('cat_', '')
        context.user_data['ticket_category'] = category
        
        await query.edit_message_text(
            f"📝 **Category:** {category}\n\n"
            "Please provide a brief subject/title for your ticket:"
        )
        
        context.user_data['expecting'] = 'subject'

    async def dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin dashboard"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Admin only.")
            return
        
        # Get ticket statistics
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        closed_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('closed',), fetch_one=True)[0]
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        
        # Get ALL tickets
        all_tickets = self.execute_query('''
            SELECT id, username, category, subject, status, created_at 
            FROM tickets ORDER BY 
                CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                created_at DESC
        ''', fetch_all=True)
        
        dashboard_text = f"📊 Admin Dashboard\n\n"
        dashboard_text += f"🎫 Total Tickets: {total_tickets}\n"
        dashboard_text += f"🟢 Open: {open_tickets}\n"
        dashboard_text += f"🔴 Closed: {closed_tickets}\n\n"
        dashboard_text += "📋 All Tickets:\n"
        
        if all_tickets:
            for ticket in all_tickets:
                status_emoji = "🟢" if ticket[4] == "open" else "🔴"
                username = ticket[1] or "Unknown"
                subject = ticket[3][:25] + "..." if len(ticket[3]) > 25 else ticket[3]
                
                # Handle datetime formatting
                if ticket[5]:
                    if hasattr(ticket[5], 'strftime'):
                        created = ticket[5].strftime("%Y-%m-%d %H:%M")
                    else:
                        created = ticket[5][:16] if len(str(ticket[5])) > 16 else str(ticket[5])
                else:
                    created = "N/A"
                    
                dashboard_text += f"{status_emoji} Ticket #{ticket[0]} - {ticket[2]}\n"
                dashboard_text += f"👤 {username} | 📝 {subject}\n"
                dashboard_text += f"📅 {created}\n"
                dashboard_text += f"🔗 /manage_{ticket[0]} (Click to manage)\n\n"
        else:
            dashboard_text += "No tickets found.\n"
        
        # Truncate if too long
        if len(dashboard_text) > 4000:
            dashboard_text = dashboard_text[:3800] + "\n\n... [List truncated - too many tickets]"
        
        keyboard = [
            [InlineKeyboardButton("🟢 Open Only", callback_data="list_open"),
             InlineKeyboardButton("🔴 Closed Only", callback_data="list_closed")],
            [InlineKeyboardButton("📈 Statistics", callback_data="stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(dashboard_text, reply_markup=reply_markup)
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed statistics - Admin only"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Admin only.")
            return
        
        # Get comprehensive statistics
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        closed_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('closed',), fetch_one=True)[0]
        
        # Today's tickets
        today_tickets = self.execute_query('''
            SELECT COUNT(*) FROM tickets 
            WHERE DATE(created_at) = CURRENT_DATE
        ''', fetch_one=True)[0]
        
        # This week's tickets  
        week_tickets_result = self.execute_query('''
            SELECT COUNT(*) FROM tickets 
            WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'
        ''', fetch_one=True)
        
        week_tickets = week_tickets_result[0] if week_tickets_result and week_tickets_result[0] is not None else 0
        
        # Category statistics
        category_stats = self.execute_query('''
            SELECT category, COUNT(*) as count, 
                   COUNT(CASE WHEN status = 'open' THEN 1 END) as open_count,
                   COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_count
            FROM tickets GROUP BY category 
            ORDER BY count DESC
        ''', fetch_all=True)
        
        # Most active users
        active_users = self.execute_query('''
            SELECT username, COUNT(*) as ticket_count 
            FROM tickets WHERE username IS NOT NULL
            GROUP BY username 
            ORDER BY ticket_count DESC LIMIT 5
        ''', fetch_all=True)
        
        stats_text = f"📈 Detailed Statistics\n\n"
        stats_text += f"🎫 Overall:\n"
        stats_text += f"• Total Tickets: {total_tickets}\n"
        stats_text += f"• Open: {open_tickets}\n"
        stats_text += f"• Closed: {closed_tickets}\n"
        stats_text += f"• Success Rate: {round((closed_tickets/total_tickets*100), 1) if total_tickets > 0 else 0}%\n\n"
        
        stats_text += f"📅 Recent Activity:\n"
        stats_text += f"• Today: {today_tickets}\n"
        stats_text += f"• This Week: {week_tickets}\n\n"
        
        if category_stats:
            stats_text += f"📊 By Category:\n"
            for category, total_count, open_count, closed_count in category_stats:
                stats_text += f"• {category}: {total_count} ({open_count} open, {closed_count} closed)\n"
            stats_text += "\n"
        
        if active_users:
            stats_text += f"👥 Most Active Users:\n"
            for username, count in active_users:
                stats_text += f"• {username}: {count} tickets\n"
        
        keyboard = [[InlineKeyboardButton("🔄 Refresh Stats", callback_data="refresh_stats"),
                     InlineKeyboardButton("📋 Dashboard", callback_data="back_dashboard")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(stats_text, reply_markup=reply_markup)

    async def categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manage categories - Main Admin only"""
        if not self.is_main_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Main Admin only.")
            return
        
        categories = self.execute_query('SELECT id, name, description FROM categories ORDER BY name', fetch_all=True)
        
        categories_text = "📂 Category Management\n\n"
        categories_text += "Current Categories:\n"
        
        for cat_id, name, description in categories:
            categories_text += f"• {name}\n"
            categories_text += f"  {description}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Category", callback_data="add_category")],
            [InlineKeyboardButton("✏️ Edit Category", callback_data="edit_category"),
             InlineKeyboardButton("🗑️ Delete Category", callback_data="delete_category")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(categories_text, reply_markup=reply_markup)

    async def admins_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manage admins - Main Admin only"""
        if not self.is_main_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Main Admin only.")
            return
        
        admins = self.execute_query('SELECT user_id, username, role, added_at FROM admins ORDER BY added_at', fetch_all=True)
        
        admins_text = "👥 Admin Management\n\n"
        admins_text += "Current Admins:\n"
        
        for user_id, username, role, added_at in admins:
            role_emoji = "👑" if role == "main_admin" else "🛡️"
            admin_name = username or f"ID: {user_id}"
            
            # Format date
            if hasattr(added_at, 'strftime'):
                date_str = added_at.strftime("%Y-%m-%d")
            else:
                date_str = str(added_at)[:10] if len(str(added_at)) > 10 else str(added_at)
            
            admins_text += f"{role_emoji} {admin_name} ({role})\n"
            admins_text += f"    Added: {date_str}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
            [InlineKeyboardButton("🗑️ Remove Admin", callback_data="remove_admin")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(admins_text, reply_markup=reply_markup)

    # Category Management Handlers
    async def add_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start adding new category"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_main_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Main Admin only.")
            return
        
        context.user_data['adding_category'] = True
        
        await query.edit_message_text(
            "➕ Add New Category\n\n"
            "Please enter the category name:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_category")
            ]])
        )

    async def handle_category_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle category name input"""
        if not context.user_data.get('adding_category'):
            return
        
        if not self.is_main_admin(update.effective_user.id):
            return
        
        category_name = update.message.text.strip()
        
        if len(category_name) > 50:
            await update.message.reply_text("❌ Category name too long. Maximum 50 characters.")
            return
        
        context.user_data['category_name'] = category_name
        context.user_data['adding_category'] = False
        context.user_data['adding_description'] = True
        
        await update.message.reply_text(
            f"✅ Category name: {category_name}\n\n"
            "Now enter a description for this category:"
        )

    async def handle_category_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle category description input"""
        if not context.user_data.get('adding_description'):
            return
        
        if not self.is_main_admin(update.effective_user.id):
            return
        
        description = update.message.text.strip()
        category_name = context.user_data.get('category_name')
        
        if len(description) > 200:
            await update.message.reply_text("❌ Description too long. Maximum 200 characters.")
            return
        
        try:
            # Add category to database
            self.execute_query(
                'INSERT INTO categories (name, description) VALUES (?, ?)',
                (category_name, description)
            )
            
            await update.message.reply_text(
                f"✅ Category Added Successfully!\n\n"
                f"Name: {category_name}\n"
                f"Description: {description}\n\n"
                f"Users can now select this category when creating tickets."
            )
            
        except Exception as e:
            if "UNIQUE constraint failed" in str(e) or "duplicate key" in str(e):
                await update.message.reply_text(f"❌ Category '{category_name}' already exists.")
            else:
                await update.message.reply_text(f"❌ Error adding category: {str(e)}")
        
        # Clear context
        context.user_data.clear()

    # Admin Management Handlers  
    async def add_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start adding new admin"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_main_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Main Admin only.")
            return
        
        context.user_data['adding_admin'] = True
        
        await query.edit_message_text(
            "➕ Add New Admin\n\n"
            "Please send me the user ID of the person you want to make admin.\n\n"
            "How to get User ID:\n"
            "1. Ask the person to send a message to @userinfobot\n"
            "2. The bot will reply with their User ID\n"
            "3. Send me that number",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_admin")
            ]])
        )

    async def handle_admin_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin ID input"""
        if not context.user_data.get('adding_admin'):
            return
        
        if not self.is_main_admin(update.effective_user.id):
            return
        
        try:
            user_id = int(update.message.text.strip())
            
            # Check if already admin
            existing = self.execute_query('SELECT role FROM admins WHERE user_id = ?', (user_id,), fetch_one=True)
            if existing:
                await update.message.reply_text(f"❌ User {user_id} is already an admin ({existing[0]}).")
                context.user_data.clear()
                return
            
            # Add admin
            self.execute_query(
                'INSERT INTO admins (user_id, username, role, added_by) VALUES (?, ?, ?, ?)',
                (user_id, f"Admin_{user_id}", "admin", update.effective_user.id)
            )
            
            await update.message.reply_text(
                f"✅ Admin Added Successfully!\n\n"
                f"User ID: {user_id}\n"
                f"Role: Admin\n"
                f"Added by: {update.effective_user.first_name}\n\n"
                f"They can now use admin commands and manage tickets."
            )
            
            # Try to notify the new admin
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 Congratulations!\n\n"
                         f"You have been made an admin of the support system!\n\n"
                         f"You can now:\n"
                         f"• Use /dashboard to manage tickets\n"
                         f"• Reply to user tickets\n"
                         f"• View support statistics\n\n"
                         f"Added by: {update.effective_user.first_name}"
                )
            except:
                pass  # User might not have started the bot yet
                
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID. Please enter numbers only.")
            return
        except Exception as e:
            await update.message.reply_text(f"❌ Error adding admin: {str(e)}")
        
        # Clear context
        context.user_data.clear()

    # Admin Menu System
    async def setup_admin_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Setup persistent admin menu in the admin group"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Admin only.")
            return
        
        # Get current status
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        
        # Update status based on workload
        if open_tickets == 0:
            status = "🟢 All Clear - No Open Tickets"
        elif open_tickets <= 5:
            status = f"🟡 Normal Load - {open_tickets} Open Tickets"
        elif open_tickets <= 15:
            status = f"🟠 Busy - {open_tickets} Open Tickets"
        else:
            status = f"🔴 High Load - {open_tickets} Open Tickets"
        
        menu_text = "🎛️ Support Admin Control Panel 🎛️\n\n"
        menu_text += "Welcome to the Support Bot Command Center!\n"
        menu_text += "Use the buttons below for quick access to all admin functions.\n\n"
        menu_text += "Quick Stats:\n"
        menu_text += f"• Total Tickets: {total_tickets}\n"
        menu_text += f"• Open Tickets: {open_tickets}\n"
        menu_text += f"• System Status: {status}\n\n"
        menu_text += f"Last Updated: {datetime.now().strftime('%H:%M:%S')}"
        
        # Main menu keyboard
        keyboard = [
            [InlineKeyboardButton("📊 Dashboard", callback_data="menu_dashboard"),
             InlineKeyboardButton("📈 Statistics", callback_data="menu_stats")],
            [InlineKeyboardButton("🟢 Open Tickets", callback_data="menu_open"),
             InlineKeyboardButton("🔴 Closed Tickets", callback_data="menu_closed")],
            [InlineKeyboardButton("📂 Categories", callback_data="menu_categories"),
             InlineKeyboardButton("👥 Admins", callback_data="menu_admins")],
            [InlineKeyboardButton("📖 Help Guide", callback_data="menu_help"),
             InlineKeyboardButton("🔄 Refresh Menu", callback_data="menu_refresh")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(menu_text, reply_markup=reply_markup)

    async def handle_menu_actions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin menu button clicks"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        action = query.data.replace('menu_', '')
        
        if action == 'dashboard':
            await self.dashboard_from_menu(query, context)
        elif action == 'stats':
            await self.stats_from_menu(query, context)
        elif action == 'open':
            await self.list_open_tickets_from_menu(query, context)
        elif action == 'closed':
            await self.list_closed_tickets_from_menu(query, context)
        elif action == 'categories':
            if self.is_main_admin(query.from_user.id):
                await self.categories_from_menu(query, context)
            else:
                await query.edit_message_text("❌ Categories management is Main Admin only.")
        elif action == 'admins':
            if self.is_main_admin(query.from_user.id):
                await self.admins_management_from_menu(query, context)
            else:
                await query.edit_message_text("❌ Admin management is Main Admin only.")
        elif action == 'help':
            await self.show_help_menu(query, context)
        elif action == 'refresh':
            await self.refresh_admin_menu(query, context)

    async def dashboard_from_menu(self, query, context):
        """Show dashboard from menu"""
        # Get ticket statistics
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        closed_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('closed',), fetch_one=True)[0]
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        
        # Get recent tickets (last 5)
        recent_tickets = self.execute_query('''
            SELECT id, username, category, subject, status, created_at 
            FROM tickets ORDER BY 
                CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                created_at DESC 
            LIMIT 5
        ''', fetch_all=True)
        
        dashboard_text = f"📊 Quick Dashboard Overview\n\n"
        dashboard_text += f"🎫 Statistics:\n"
        dashboard_text += f"• Total: {total_tickets}\n"
        dashboard_text += f"• Open: {open_tickets}\n"
        dashboard_text += f"• Closed: {closed_tickets}\n\n"
        
        if recent_tickets:
            dashboard_text += "📋 Recent Tickets:\n"
            for ticket in recent_tickets:
                status_emoji = "🟢" if ticket[4] == "open" else "🔴"
                username = ticket[1] or "Unknown"
                subject = ticket[3][:30] + "..." if len(ticket[3]) > 30 else ticket[3]
                
                dashboard_text += f"{status_emoji} #{ticket[0]} - {ticket[2]}\n"
                dashboard_text += f"    👤 {username} | 📝 {subject}\n"
        else:
            dashboard_text += "📋 No tickets found.\n"
        
        # Back to menu + full dashboard buttons
        keyboard = [
            [InlineKeyboardButton("📊 Full Dashboard", callback_data="back_dashboard")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_refresh")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(dashboard_text, reply_markup=reply_markup)

    async def stats_from_menu(self, query, context):
        """Show quick stats from menu"""
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        closed_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('closed',), fetch_one=True)[0]
        
        # Today's tickets
        today_tickets = self.execute_query('''
            SELECT COUNT(*) FROM tickets 
            WHERE DATE(created_at) = CURRENT_DATE
        ''', fetch_one=True)[0]
        
        stats_text = f"📈 Quick Statistics\n\n"
        stats_text += f"🎫 Overview:\n"
        stats_text += f"• Total Tickets: {total_tickets}\n"
        stats_text += f"• Open: {open_tickets}\n"
        stats_text += f"• Closed: {closed_tickets}\n"
        stats_text += f"• Success Rate: {round((closed_tickets/total_tickets*100), 1) if total_tickets > 0 else 0}%\n\n"
        stats_text += f"📅 Today: {today_tickets} new tickets\n"
        
        keyboard = [
            [InlineKeyboardButton("📊 Detailed Stats", callback_data="detailed_stats")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_refresh")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup)

    async def show_help_menu(self, query, context):
        """Show help and quick reference"""
        help_text = "📖 Support Bot Quick Reference\n\n"
        help_text += "🎫 Ticket Management:\n"
        help_text += "• Click ticket numbers to open details\n"
        help_text += "• Use Reply button to message users\n"
        help_text += "• Take button assigns ticket to you\n"
        help_text += "• Close button marks as resolved\n\n"
        
        help_text += "📊 Navigation:\n"
        help_text += "• Dashboard = All tickets overview\n"
        help_text += "• Statistics = Detailed metrics\n"
        help_text += "• Open/Closed = Filtered views\n"
        help_text += "• Categories = Manage ticket types\n"
        help_text += "• Admins = Team management\n\n"
        
        help_text += "⚡ Quick Tips:\n"
        help_text += "• Always Take tickets before replying\n"
        help_text += "• Check full conversation before responding\n"
        help_text += "• Close tickets when issues are resolved\n"
        help_text += "• Use professional, helpful language\n\n"
        
        help_text += "🆘 Need Help?\n"
        help_text += "• Contact Main Admin for permissions\n"
        help_text += "• Check training manual for detailed guide\n"
        help_text += "• Use menu buttons for easy navigation"
        
        keyboard = [
            [InlineKeyboardButton("📋 Training Manual", callback_data="show_manual")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_refresh")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(help_text, reply_markup=reply_markup)

    async def show_manual(self, query, context):
        """Show condensed training manual"""
        manual_text = "📚 Essential Admin Commands\n\n"
        manual_text += "🎮 Commands:\n"
        manual_text += "• /dashboard - Main ticket overview\n"
        manual_text += "• /stats - Detailed statistics\n"
        manual_text += "• /categories - Manage categories (Main Admin)\n"
        manual_text += "• /admins - Manage team (Main Admin)\n"
        manual_text += "• /menu - Admin control panel\n\n"
        
        manual_text += "🔄 Workflow:\n"
        manual_text += "1. Check Dashboard → See new tickets\n"
        manual_text += "2. Click /manage_X → Open ticket details\n"
        manual_text += "3. Take Ticket → Assign to yourself\n"
        manual_text += "4. Reply to User → Provide solution\n"
        manual_text += "5. Close Ticket → Mark as resolved\n\n"
        
        manual_text += "💬 Response Templates:\n"
        manual_text += "• Opening: 'Thank you for contacting support...'\n"
        manual_text += "• Solution: 'Here's how to resolve this...'\n"
        manual_text += "• Closing: 'Is there anything else I can help with?'\n\n"
        
        manual_text += "⏰ Response Goals:\n"
        manual_text += "• First Response: Within 2 hours\n"
        manual_text += "• Follow-up: Within 24 hours\n"
        manual_text += "• Resolution: Within 48 hours"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Help", callback_data="menu_help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(manual_text, reply_markup=reply_markup)

    async def refresh_admin_menu(self, query, context):
        """Refresh the main admin menu"""
        # Get current status
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        
        # Update status based on workload
        if open_tickets == 0:
            status = "🟢 All Clear - No Open Tickets"
        elif open_tickets <= 5:
            status = f"🟡 Normal Load - {open_tickets} Open Tickets"
        elif open_tickets <= 15:
            status = f"🟠 Busy - {open_tickets} Open Tickets"
        else:
            status = f"🔴 High Load - {open_tickets} Open Tickets"
        
        menu_text = "🎛️ Support Admin Control Panel 🎛️\n\n"
        menu_text += "Welcome to the Support Bot Command Center!\n"
        menu_text += "Use the buttons below for quick access to all admin functions.\n\n"
        menu_text += "Quick Stats:\n"
        menu_text += f"• Total Tickets: {total_tickets}\n"
        menu_text += f"• Open Tickets: {open_tickets}\n"
        menu_text += f"• System Status: {status}\n\n"
        menu_text += f"Last Updated: {datetime.now().strftime('%H:%M:%S')}"
        
        # Main menu keyboard
        keyboard = [
            [InlineKeyboardButton("📊 Dashboard", callback_data="menu_dashboard"),
             InlineKeyboardButton("📈 Statistics", callback_data="menu_stats")],
            [InlineKeyboardButton("🟢 Open Tickets", callback_data="menu_open"),
             InlineKeyboardButton("🔴 Closed Tickets", callback_data="menu_closed")],
            [InlineKeyboardButton("📂 Categories", callback_data="menu_categories"),
             InlineKeyboardButton("👥 Admins", callback_data="menu_admins")],
            [InlineKeyboardButton("📖 Help Guide", callback_data="menu_help"),
             InlineKeyboardButton("🔄 Refresh Menu", callback_data="menu_refresh")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(menu_text, reply_markup=reply_markup)

    async def handle_admin_input_enhanced(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Enhanced handler for category, admin inputs, and regular messages"""
        user = update.effective_user
        
        # Handle category creation
        if context.user_data.get('adding_category'):
            await self.handle_category_input(update, context)
            return
        
        if context.user_data.get('adding_description'):
            await self.handle_category_description(update, context)
            return
        
        # Handle admin creation
        if context.user_data.get('adding_admin'):
            await self.handle_admin_input(update, context)
            return
        
        # Check if admin is replying to ticket
        if 'replying_to_ticket' in context.user_data and self.is_admin(user.id):
            await self.handle_admin_reply(update, context)
            return
        
        if 'expecting' in context.user_data:
            if context.user_data['expecting'] == 'subject':
                context.user_data['ticket_subject'] = update.message.text
                context.user_data['expecting'] = 'description'
                
                await update.message.reply_text(
                    "📋 **Subject:** " + update.message.text + "\n\n"
                    "Now please describe your issue in detail. You can also send images if needed:"
                )
                
            elif context.user_data['expecting'] == 'description':
                await self.create_ticket_final(update, context, update.message.text)
                
        else:
            # Check if this is a message for an open ticket
            await self.handle_ticket_message(update, context)

    # Ersetzte handle_photo Methode
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages"""
        user = update.effective_user
        
        # Check if admin is replying to ticket with photo
        if 'replying_to_ticket' in context.user_data and self.is_admin(user.id):
            await self.handle_admin_reply(update, context, message_type='photo')
            return
            
        if context.user_data.get('expecting') == 'description':
            caption = update.message.caption or "Image attachment"
            await self.create_ticket_final(update, context, caption, update.message.photo[-1].file_id)
        else:
            await self.handle_ticket_message(update, context, message_type='photo')

    async def handle_admin_reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                 message_type: str = 'text'):
        """Handle admin reply to ticket"""
        user = update.effective_user
        ticket_id = context.user_data['replying_to_ticket']
        
        # Get ticket details
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            await update.message.reply_text("❌ Ticket not found.")
            context.user_data.pop('replying_to_ticket', None)
            return
        
        ticket_user_id = ticket[1]
        message_text = update.message.text if message_type == 'text' else update.message.caption or "Image from support"
        file_id = None
        if message_type == 'photo':
            file_id = update.message.photo[-1].file_id
        
        # Save admin message to database using execute_query
        self.execute_query('''
            INSERT INTO ticket_messages (ticket_id, user_id, username, message, message_type, file_id, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, TRUE)
        ''', (ticket_id, user.id, user.username or user.first_name, message_text, message_type, file_id))
        
        # Update ticket timestamp
        self.execute_query('''
            UPDATE tickets SET updated_at = CURRENT_TIMESTAMP WHERE id = ?
        ''', (ticket_id,))
        
        # Send message to user
        try:
            support_text = f"🎫 **Support Team Response (Ticket #{ticket_id})**\n\n{message_text}"
            
            if message_type == 'photo' and file_id:
                await context.bot.send_photo(
                    chat_id=ticket_user_id,
                    photo=file_id,
                    caption=support_text
                )
            else:
                await context.bot.send_message(
                    chat_id=ticket_user_id,
                    text=support_text
                )
            
            await update.message.reply_text(f"✅ Reply sent to user for ticket #{ticket_id}")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send message to user: {str(e)}")
        
        # Clear reply mode
        context.user_data.pop('replying_to_ticket', None)

    async def create_ticket_final(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                  description: str, file_id: str = None):
        """Create the final ticket"""
        user = update.effective_user
        category = context.user_data.get('ticket_category')
        subject = context.user_data.get('ticket_subject')
        
        if not category or not subject:
            await update.message.reply_text("❌ Error creating ticket. Please start over with /ticket")
            return
        
        # Create ticket in database using execute_query
        ticket_id = self.execute_query('''
            INSERT INTO tickets (user_id, username, category, subject, description, status)
            VALUES (?, ?, ?, ?, ?, 'open')
        ''', (user.id, user.username or user.first_name, category, subject, description))
        
        # Add initial message
        self.execute_query('''
            INSERT INTO ticket_messages (ticket_id, user_id, username, message, message_type, file_id, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, FALSE)
        ''', (ticket_id, user.id, user.username or user.first_name, description, 
              'photo' if file_id else 'text', file_id))
        
        # Clear user data
        context.user_data.clear()
        context.user_data['active_ticket'] = ticket_id
        
        # Send confirmation to user
        ticket_text = f"✅ **Ticket Created Successfully!**\n\n"
        ticket_text += f"🎫 **Ticket ID:** #{ticket_id}\n"
        ticket_text += f"📂 **Category:** {category}\n"
        ticket_text += f"📝 **Subject:** {subject}\n"
        ticket_text += f"📋 **Description:** {description}\n\n"
        ticket_text += "An admin will respond to you soon. You can continue sending messages here."
        
        keyboard = [[InlineKeyboardButton("🔒 Close Ticket", callback_data=f"close_{ticket_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(ticket_text, reply_markup=reply_markup)
        
        # Notify admins
        await self.notify_admins_new_ticket(context, ticket_id, user, category, subject, description)

    async def notify_admins_new_ticket(self, context: ContextTypes.DEFAULT_TYPE, 
                                       ticket_id: int, user, category: str, subject: str, description: str):
        """Notify admins about new ticket"""
        admin_text = f"🆕 **New Support Ticket**\n\n"
        admin_text += f"🎫 **ID:** #{ticket_id}\n"
        admin_text += f"👤 **User:** {user.first_name} (@{user.username or 'N/A'})\n"
        admin_text += f"📂 **Category:** {category}\n"
        admin_text += f"📝 **Subject:** {subject}\n"
        admin_text += f"📋 **Description:** {description[:200]}{'...' if len(description) > 200 else ''}\n"
        
        keyboard = [
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
             InlineKeyboardButton("👁️ View", callback_data=f"view_{ticket_id}")],
            [InlineKeyboardButton("✅ Take", callback_data=f"take_{ticket_id}"),
             InlineKeyboardButton("🔒 Close", callback_data=f"admin_close_{ticket_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.send_message(
                chat_id=self.admin_group_id,
                text=admin_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error sending admin notification: {e}")

    # Ersetzte handle_ticket_message Methode
    async def handle_ticket_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle messages in active tickets"""
        user = update.effective_user
        
        active_ticket = self.execute_query('''
            SELECT id FROM tickets 
            WHERE user_id = ? AND status = 'open' 
            ORDER BY updated_at DESC LIMIT 1
        ''', (user.id,), fetch_one=True)
        
        if not active_ticket:
            return  # No active ticket
        
        ticket_id = active_ticket[0]
        
        message_type = 'text'
        file_id = None
        message_text = update.message.text
        
        if update.message.photo:
            message_type = 'photo'
            file_id = update.message.photo[-1].file_id
            message_text = update.message.caption or "Image attachment"
        
        # Save message to database
        self.execute_query('''
            INSERT INTO ticket_messages (ticket_id, user_id, username, message, message_type, file_id, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, FALSE)
        ''', (ticket_id, user.id, user.username or user.first_name, message_text, message_type, file_id))
        
        # Update ticket timestamp
        self.execute_query('''
            UPDATE tickets SET updated_at = CURRENT_TIMESTAMP WHERE id = ?
        ''', (ticket_id,))
        
        # Notify admins about the update
        await self.notify_admins_ticket_update_with_photo(context, ticket_id, user, message_text, file_id)
        
        # Send confirmation to user
        await update.message.reply_text(
            f"✅ Message added to ticket #{ticket_id}\n"
            f"An admin will respond to you soon."
        )

    # Neue Methode
    async def notify_admins_ticket_update_with_photo(self, context: ContextTypes.DEFAULT_TYPE, 
                                                     ticket_id: int, user, message: str, file_id: str):
        """Notify admins about ticket updates, including photo"""
        # Get ticket details
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return
        
        category = ticket[3]
        subject = ticket[4]
        
        admin_text = f"💬 **Ticket Update**\n\n"
        admin_text += f"🎫 **Ticket:** #{ticket_id}\n"
        admin_text += f"👤 **User:** {user.first_name} (@{user.username or 'N/A'})\n"
        admin_text += f"📂 **Category:** {category}\n"
        admin_text += f"📝 **Subject:** {subject}\n"
        admin_text += f"💭 **New Message:**\n{message[:300]}{'...' if len(message) > 300 else ''}"
        
        keyboard = [
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
             InlineKeyboardButton("👁️ View Full", callback_data=f"view_{ticket_id}")],
            [InlineKeyboardButton("🔒 Close", callback_data=f"admin_close_{ticket_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            if file_id:
                await context.bot.send_photo(
                    chat_id=self.admin_group_id,
                    photo=file_id,
                    caption=admin_text,
                    reply_markup=reply_markup
                )
            else:
                await context.bot.send_message(
                    chat_id=self.admin_group_id,
                    text=admin_text,
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Error sending admin notification: {e}")

    # Alle fehlenden Methoden...
    async def my_tickets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user's tickets"""
        user = update.effective_user
        
        # Get user's tickets
        tickets = self.execute_query('''
            SELECT id, category, subject, status, created_at, updated_at
            FROM tickets WHERE user_id = ? 
            ORDER BY updated_at DESC
        ''', (user.id,), fetch_all=True)
        
        if not tickets:
            await update.message.reply_text(
                "📋 You don't have any support tickets yet.\n\n"
                "Use /ticket to create your first support ticket!"
            )
            return
        
        tickets_text = f"🎫 **Your Support Tickets**\n\n"
        
        for ticket in tickets:
            ticket_id, category, subject, status, created_at, updated_at = ticket
            status_emoji = "🟢" if status == "open" else "🔴"
            
            # Handle datetime formatting
            if created_at:
                if hasattr(created_at, 'strftime'):
                    created_str = created_at.strftime("%Y-%m-%d %H:%M")
                else:
                    created_str = str(created_at)[:16]
            else:
                created_str = "Unknown"
            
            tickets_text += f"{status_emoji} **Ticket #{ticket_id}**\n"
            tickets_text += f"📂 Category: {category}\n"
            tickets_text += f"📝 Subject: {subject}\n"
            tickets_text += f"📅 Created: {created_str}\n"
            tickets_text += f"🔧 Status: {status.title()}\n\n"
        
        keyboard = [[InlineKeyboardButton("🆕 Create New Ticket", callback_data="create_new")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(tickets_text, reply_markup=reply_markup)
        
    async def manage_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /manage_X commands"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Admin only.")
            return
        
        # Extract ticket ID from command
        command = update.message.text.split('@')[0]
        try:
            ticket_id = int(command.replace('/manage_', ''))
        except ValueError:
            await update.message.reply_text("❌ Invalid ticket ID.")
            return
            
        # Get ticket details
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            await update.message.reply_text(f"❌ Ticket #{ticket_id} not found.")
            return
        
        # Show ticket management interface
        await self.show_ticket_management_interface(update, context, ticket_id, ticket)

    async def show_ticket_management_interface(self, update, context, ticket_id, ticket):
        """Show ticket management interface"""
        ticket_id, user_id, username, category, subject, description, status, assigned_admin, created_at, updated_at = ticket
        
        ticket_text = f"🎫 **Ticket #{ticket_id} Management**\n\n"
        ticket_text += f"👤 **User:** {username or 'Unknown'}\n"
        ticket_text += f"📂 **Category:** {category}\n"
        ticket_text += f"📝 **Subject:** {subject}\n"
        ticket_text += f"🔧 **Status:** {status.title()}\n\n"
        ticket_text += f"📋 **Description:**\n{description[:200]}{'...' if len(description) > 200 else ''}"
        
        keyboard = [
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
             InlineKeyboardButton("👁️ View Full", callback_data=f"view_{ticket_id}")],
            [InlineKeyboardButton("✅ Take", callback_data=f"take_{ticket_id}"),
             InlineKeyboardButton("🔒 Close", callback_data=f"admin_close_{ticket_id}")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(ticket_text, reply_markup=reply_markup)

    async def reply_to_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle reply button"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        ticket_id = int(query.data.split('_')[1])
        context.user_data['replying_to_ticket'] = ticket_id
        
        await query.edit_message_text(
            f"💬 **Reply Mode Activated**\n\n"
            f"🎫 Ticket: #{ticket_id}\n\n"
            f"Send your reply message now (text or photo).\n"
            f"Your next message will be sent to the user.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel Reply", callback_data=f"cancel_reply_{ticket_id}")
            ]])
        )

    async def view_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View full ticket details"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        ticket_id = int(query.data.split('_')[1])
        ticket = self.get_ticket(ticket_id)
        
        if not ticket:
            await query.edit_message_text("❌ Ticket not found.")
            return
        
        # Show full ticket details
        messages = self.get_ticket_messages(ticket_id)
        
        view_text = f"🎫 **Full Ticket #{ticket_id}**\n\n"
        view_text += f"👤 User: {ticket[2] or 'Unknown'}\n"
        view_text += f"📂 Category: {ticket[3]}\n"
        view_text += f"📝 Subject: {ticket[4]}\n"
        view_text += f"📋 Description: {ticket[5]}\n\n"
        
        if messages:
            view_text += "💬 **Conversation:**\n"
            for msg in messages[-5:]: # Last 5 messages
                sender = "🛡️ Admin" if msg[5] else "👤 User"
                view_text += f"{sender}: {msg[2][:100]}{'...' if len(msg[2]) > 100 else ''}\n"
        
        keyboard = [
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
             InlineKeyboardButton("🔒 Close", callback_data=f"admin_close_{ticket_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_dashboard")]
        ]
        
        await query.edit_message_text(view_text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def take_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Take/assign ticket to admin"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        ticket_id = int(query.data.split('_')[1])
        admin_id = query.from_user.id
        admin_name = query.from_user.first_name
        
        # Assign ticket to admin
        self.execute_query('''
            UPDATE tickets 
            SET assigned_admin = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (admin_id, ticket_id))
        
        await query.edit_message_text(
            f"✅ **Ticket #{ticket_id} Assigned**\n\n"
            f"Assigned to: {admin_name}\n\n"
            f"You can now reply to the user.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💬 Reply Now", callback_data=f"reply_{ticket_id}"),
                InlineKeyboardButton("🔙 Dashboard", callback_data="back_dashboard")
            ]])
        )

    async def close_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Close ticket"""
        query = update.callback_query
        await query.answer()
        
        if query.data.startswith('admin_close_'):
            if not self.is_admin(query.from_user.id):
                await query.edit_message_text("❌ Access denied. Admin only.")
                return
            ticket_id = int(query.data.split('_')[2])
        else:
            ticket_id = int(query.data.split('_')[1])
        
        # Close the ticket
        self.execute_query('''
            UPDATE tickets 
            SET status = 'closed', closed_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (ticket_id,))
        
        await query.edit_message_text(
            f"✅ **Ticket #{ticket_id} Closed**\n\n"
            f"The ticket has been marked as resolved.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")
            ]])
        )

    async def list_open_tickets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List only open tickets"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        open_tickets = self.execute_query('''
            SELECT id, username, category, subject, created_at 
            FROM tickets WHERE status = 'open'
            ORDER BY created_at DESC LIMIT 10
        ''', fetch_all=True)
        
        if not open_tickets:
            tickets_text = "🟢 **Open Tickets**\n\nNo open tickets! 🎉"
        else:
            tickets_text = f"🟢 **Open Tickets** ({len(open_tickets)})\n\n"
            for ticket in open_tickets:
                tickets_text += f"🎫 #{ticket[0]} - {ticket[2]}\n"
                tickets_text += f"👤 {ticket[1] or 'Unknown'} | 📝 {ticket[3][:30]}...\n"
                tickets_text += f"🔗 /manage_{ticket[0]}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("📊 All Tickets", callback_data="back_dashboard"),
             InlineKeyboardButton("🔴 Closed", callback_data="list_closed")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="list_open")]
        ]
        
        await query.edit_message_text(tickets_text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def list_closed_tickets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List only closed tickets"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        closed_tickets = self.execute_query('''
            SELECT id, username, category, subject, closed_at 
            FROM tickets WHERE status = 'closed'
            ORDER BY closed_at DESC LIMIT 10
        ''', fetch_all=True)
        
        if not closed_tickets:
            tickets_text = "🔴 **Closed Tickets**\n\nNo closed tickets found."
        else:
            tickets_text = f"🔴 **Closed Tickets** ({len(closed_tickets)})\n\n"
            for ticket in closed_tickets:
                tickets_text += f"🎫 #{ticket[0]} - {ticket[2]}\n"
                tickets_text += f"👤 {ticket[1] or 'Unknown'} | 📝 {ticket[3][:30]}...\n\n"
        
        keyboard = [
            [InlineKeyboardButton("📊 All Tickets", callback_data="back_dashboard"),
             InlineKeyboardButton("🟢 Open", callback_data="list_open")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="list_closed")]
        ]
        
        await query.edit_message_text(tickets_text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show statistics from callback"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        # Redirect to stats method
        await self.stats_from_menu(query, context)

    async def back_to_dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Navigate back to dashboard"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        context.user_data.clear()
        await self.dashboard_from_menu(query, context)

    async def back_to_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Navigate back to ticket"""
        query = update.callback_query
        await query.answer()
        
        ticket_id = int(query.data.split('_')[-1])
        context.user_data.pop('replying_to_ticket', None)
        
        # Show ticket details again
        ticket = self.get_ticket(ticket_id)
        if ticket:
            await self.show_ticket_management_interface_callback(query, context, ticket_id, ticket)

    async def show_ticket_management_interface_callback(self, query, context, ticket_id, ticket):
        """Show ticket management via callback"""
        ticket_text = f"🎫 **Ticket #{ticket_id}**\n\n"
        ticket_text += f"👤 User: {ticket[2] or 'Unknown'}\n"
        ticket_text += f"📂 Category: {ticket[3]}\n"
        ticket_text += f"📝 Subject: {ticket[4]}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
             InlineKeyboardButton("👁️ View", callback_data=f"view_{ticket_id}")],
            [InlineKeyboardButton("✅ Take", callback_data=f"take_{ticket_id}"),
             InlineKeyboardButton("🔒 Close", callback_data=f"admin_close_{ticket_id}")],
            [InlineKeyboardButton("🔙 Dashboard", callback_data="back_dashboard")]
        ]
        
        await query.edit_message_text(ticket_text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def cancel_operation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel current operation"""
        query = update.callback_query
        await query.answer()
        
        context.user_data.clear()
        
        await query.edit_message_text(
            "❌ **Operation Cancelled**\n\nNo changes made.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="back_dashboard")
            ]])
        )

    # Menu helper methods für categories und admins
    async def list_open_tickets_from_menu(self, query, context):
        """Show open tickets from menu"""
        open_tickets = self.execute_query('''
            SELECT id, username, category, subject 
            FROM tickets WHERE status = 'open'
            ORDER BY created_at DESC LIMIT 5
        ''', fetch_all=True)
        
        if not open_tickets:
            text = "🟢 Open Tickets (0)\n\nNo open tickets! 🎉"
        else:
            text = f"🟢 Open Tickets ({len(open_tickets)})\n\n"
            for ticket in open_tickets:
                text += f"🎫 #{ticket[0]} - {ticket[2]}\n"
                text += f"👤 {ticket[1] or 'Unknown'}\n"
                text += f"🔗 /manage_{ticket[0]}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("📊 Full Dashboard", callback_data="menu_dashboard")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_refresh")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def list_closed_tickets_from_menu(self, query, context):
        """Show closed tickets from menu"""
        closed_tickets = self.execute_query('''
            SELECT id, username, category, subject 
            FROM tickets WHERE status = 'closed'
            ORDER BY closed_at DESC LIMIT 5
        ''', fetch_all=True)
        
        if not closed_tickets:
            text = "🔴 Closed Tickets (0)\n\nNo closed tickets yet."
        else:
            text = f"🔴 Recent Closed Tickets ({len(closed_tickets)})\n\n"
            for ticket in closed_tickets:
                text += f"🎫 #{ticket[0]} - {ticket[2]}\n"
                text += f"👤 {ticket[1] or 'Unknown'}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("📊 Full Dashboard", callback_data="menu_dashboard")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_refresh")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def categories_from_menu(self, query, context):
        """Show categories from menu"""
        categories = self.execute_query('SELECT name, description FROM categories ORDER BY name', fetch_all=True)
        
        text = "📂 Category Management\n\n"
        text += "Current Categories:\n"
        for name, desc in categories:
            text += f"• {name}\n  {desc}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Category", callback_data="add_category")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_refresh")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def admins_management_from_menu(self, query, context):
        """Show admins from menu"""
        admins = self.execute_query('SELECT user_id, username, role FROM admins ORDER BY role', fetch_all=True)
        
        text = "👥 Admin Management\n\n"
        text += "Current Admins:\n"
        for user_id, username, role in admins:
            role_emoji = "👑" if role == "main_admin" else "🛡️"
            text += f"{role_emoji} {username or f'ID:{user_id}'} ({role})\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_refresh")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def detailed_stats_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed stats from callback"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        await self.stats_callback_version(query, context)

    async def stats_callback_version(self, query, context):
        """Show detailed statistics - callback version"""
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        closed_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('closed',), fetch_one=True)[0]
        
        today_tickets = self.execute_query('''
            SELECT COUNT(*) FROM tickets 
            WHERE DATE(created_at) = CURRENT_DATE
        ''', fetch_one=True)[0]
        
        week_tickets_result = self.execute_query('''
            SELECT COUNT(*) FROM tickets 
            WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'
        ''', fetch_one=True)
        
        week_tickets = week_tickets_result[0] if week_tickets_result and week_tickets_result[0] is not None else 0
        
        category_stats = self.execute_query('''
            SELECT category, COUNT(*) as count, 
                   COUNT(CASE WHEN status = 'open' THEN 1 END) as open_count,
                   COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_count
            FROM tickets GROUP BY category 
            ORDER BY count DESC
        ''', fetch_all=True)
        
        active_users = self.execute_query('''
            SELECT username, COUNT(*) as ticket_count 
            FROM tickets WHERE username IS NOT NULL
            GROUP BY username 
            ORDER BY ticket_count DESC LIMIT 5
        ''', fetch_all=True)
        
        stats_text = f"📈 Detailed Statistics\n\n"
        stats_text += f"🎫 Overall:\n"
        stats_text += f"• Total Tickets: {total_tickets}\n"
        stats_text += f"• Open: {open_tickets}\n"
        stats_text += f"• Closed: {closed_tickets}\n"
        stats_text += f"• Success Rate: {round((closed_tickets/total_tickets*100), 1) if total_tickets > 0 else 0}%\n\n"
        
        stats_text += f"📅 Recent Activity:\n"
        stats_text += f"• Today: {today_tickets}\n"
        stats_text += f"• This Week: {week_tickets}\n\n"
        
        if category_stats:
            stats_text += f"📊 By Category:\n"
            for category, total_count, open_count, closed_count in category_stats:
                stats_text += f"• {category}: {total_count} ({open_count} open, {closed_count} closed)\n"
            stats_text += "\n"
        
        if active_users:
            stats_text += f"👥 Most Active Users:\n"
            for username, count in active_users:
                stats_text += f"• {username}: {count} tickets\n"
        
        keyboard = [[InlineKeyboardButton("📋 Dashboard", callback_data="back_dashboard")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup)

    async def full_dashboard_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show full dashboard from callback"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        await self.dashboard_callback_version(query, context)

    async def dashboard_callback_version(self, query, context):
        """Admin dashboard - callback version"""
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        closed_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('closed',), fetch_one=True)[0]
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        
        all_tickets = self.execute_query('''
            SELECT id, username, category, subject, status, created_at 
            FROM tickets ORDER BY 
                CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                created_at DESC
        ''', fetch_all=True)
        
        dashboard_text = f"📊 Admin Dashboard\n\n"
        dashboard_text += f"🎫 Total Tickets: {total_tickets}\n"
        dashboard_text += f"🟢 Open: {open_tickets}\n"
        dashboard_text += f"🔴 Closed: {closed_tickets}\n\n"
        dashboard_text += "📋 All Tickets:\n"
        
        if all_tickets:
            for ticket in all_tickets:
                status_emoji = "🟢" if ticket[4] == "open" else "🔴"
                username = ticket[1] or "Unknown"
                subject = ticket[3][:25] + "..." if len(ticket[3]) > 25 else ticket[3]
                
                if ticket[5]:
                    if hasattr(ticket[5], 'strftime'):
                        created = ticket[5].strftime("%Y-%m-%d %H:%M")
                    else:
                        created = ticket[5][:16] if len(str(ticket[5])) > 16 else str(ticket[5])
                else:
                    created = "N/A"
                    
                dashboard_text += f"{status_emoji} Ticket #{ticket[0]} - {ticket[2]}\n"
                dashboard_text += f"👤 {username} | 📝 {subject}\n"
                dashboard_text += f"📅 {created}\n"
                dashboard_text += f"🔗 /manage_{ticket[0]} (Click to manage)\n\n"
        else:
            dashboard_text += "No tickets found.\n"
        
        if len(dashboard_text) > 4000:
            dashboard_text = dashboard_text[:3800] + "\n\n... [List truncated - too many tickets]"
        
        keyboard = [
            [InlineKeyboardButton("🟢 Open Only", callback_data="list_open"),
             InlineKeyboardButton("🔴 Closed Only", callback_data="list_closed")],
            [InlineKeyboardButton("📈 Statistics", callback_data="stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(dashboard_text, reply_markup=reply_markup)
    
    # Run method mit den neuen Handlern
    def run(self):
        """Run the bot"""
        application = Application.builder().token(self.token).build()
        
        # Command handlers
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("ticket", self.create_ticket))
        application.add_handler(CommandHandler("dashboard", self.dashboard))
        application.add_handler(CommandHandler("mytickets", self.my_tickets))
        application.add_handler(CommandHandler("stats", self.stats))
        application.add_handler(CommandHandler("categories", self.categories))
        application.add_handler(CommandHandler("admins", self.admins_management))
        application.add_handler(CommandHandler("menu", self.setup_admin_menu))
        
        # Dynamic ticket management commands
        application.add_handler(MessageHandler(filters.Regex(r'^/manage_\d+(@\w+)?'), self.manage_ticket))
        
        # Callback handlers for ticket operations
        application.add_handler(CallbackQueryHandler(self.category_selected, pattern=r"^cat_"))
        application.add_handler(CallbackQueryHandler(self.reply_to_ticket, pattern=r"^reply_\d+"))
        application.add_handler(CallbackQueryHandler(self.view_ticket, pattern=r"^view_\d+"))
        application.add_handler(CallbackQueryHandler(self.take_ticket, pattern=r"^take_\d+"))
        application.add_handler(CallbackQueryHandler(self.close_ticket, pattern=r"^close_\d+"))
        application.add_handler(CallbackQueryHandler(self.close_ticket, pattern=r"^admin_close_\d+"))
        
        # Dashboard navigation handlers
        application.add_handler(CallbackQueryHandler(self.list_open_tickets, pattern=r"^list_open$"))
        application.add_handler(CallbackQueryHandler(self.list_closed_tickets, pattern=r"^list_closed$"))
        application.add_handler(CallbackQueryHandler(self.show_statistics, pattern=r"^stats$"))
        application.add_handler(CallbackQueryHandler(self.back_to_dashboard, pattern=r"^back_dashboard$"))
        application.add_handler(CallbackQueryHandler(self.back_to_ticket, pattern=r"^back_to_ticket_\d+$"))
        
        # Menu handlers
        application.add_handler(CallbackQueryHandler(self.handle_menu_actions, pattern=r"^menu_"))
        application.add_handler(CallbackQueryHandler(self.show_manual, pattern=r"^show_manual$"))
        application.add_handler(CallbackQueryHandler(self.stats, pattern=r"^refresh_stats$"))
        application.add_handler(CallbackQueryHandler(self.detailed_stats_callback, pattern=r"^detailed_stats$"))
        application.add_handler(CallbackQueryHandler(self.full_dashboard_callback, pattern=r"^back_dashboard$"))
        
        # Category and admin management handlers
        application.add_handler(CallbackQueryHandler(self.add_category, pattern=r"^add_category$"))
        application.add_handler(CallbackQueryHandler(self.add_admin, pattern=r"^add_admin$"))
        
        # Cancel handlers
        application.add_handler(CallbackQueryHandler(self.cancel_operation, pattern=r"^cancel_"))
        
        # Message handlers
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_admin_input_enhanced))
        application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        
        # Start the bot
        print("🤖 Support Bot is starting...")
        application.run_polling()

# Configuration
if __name__ == "__main__":
    # Get configuration from environment variables
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID"))
    ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID"))
    
    if not BOT_TOKEN or not MAIN_ADMIN_ID or not ADMIN_GROUP_ID:
        print("❌ Missing environment variables!")
        print("Required: BOT_TOKEN, MAIN_ADMIN_ID, ADMIN_GROUP_ID")
        exit(1)
    
    # Create and run bot
    bot = SupportBot(BOT_TOKEN, MAIN_ADMIN_ID, ADMIN_GROUP_ID)
    bot.run()
