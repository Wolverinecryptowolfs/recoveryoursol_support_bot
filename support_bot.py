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
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
        
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

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
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

    async def category_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle category selection"""
        query = update.callback_query
        await query.answer()
        
        category = query.data.replace('cat_', '')
        context.user_data['ticket_category'] = category
        
        await query.edit_message_text(
            f"📝 **Category:** {category}\n\n"
            "Please provide a brief subject/title for your ticket:",
            parse_mode=ParseMode.MARKDOWN
        )
        
        context.user_data['expecting'] = 'subject'

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages based on context"""
        user = update.effective_user
        
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
                    caption=support_text,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await context.bot.send_message(
                    chat_id=ticket_user_id,
                    text=support_text,
                    parse_mode=ParseMode.MARKDOWN
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
        
        await update.message.reply_text(ticket_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        
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
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Error sending admin notification: {e}")

    async def handle_ticket_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                  message_type: str = 'text'):
        """Handle messages in active ticket conversations"""
        user = update.effective_user
        
        # Get user's active ticket
        active_ticket = context.user_data.get('active_ticket')
        if not active_ticket:
            # Check if user has open tickets
            result = self.execute_query('''
                SELECT id FROM tickets WHERE user_id = ? AND status = 'open' 
                ORDER BY created_at DESC LIMIT 1
            ''', (user.id,), fetch_one=True)
            
            if result:
                active_ticket = result[0]
                context.user_data['active_ticket'] = active_ticket
            else:
                await update.message.reply_text("You don't have any active tickets. Use /ticket to create one.")
                return
        
        # Add message to ticket
        message_text = update.message.text if message_type == 'text' else update.message.caption or "Image"
        file_id = None
        if message_type == 'photo':
            file_id = update.message.photo[-1].file_id
        
        self.execute_query('''
            INSERT INTO ticket_messages (ticket_id, user_id, username, message, message_type, file_id, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, FALSE)
        ''', (active_ticket, user.id, user.username or user.first_name, message_text, message_type, file_id))
        
        # Update ticket timestamp
        self.execute_query('''
            UPDATE tickets SET updated_at = CURRENT_TIMESTAMP WHERE id = ?
        ''', (active_ticket,))
        
        # Notify admins
        await self.notify_admins_ticket_update(context, active_ticket, user, message_text, message_type, file_id)

    async def notify_admins_ticket_update(self, context: ContextTypes.DEFAULT_TYPE, 
                                        ticket_id: int, user, message: str, 
                                        message_type: str, file_id: str = None):
        """Notify admins about ticket updates"""
        admin_text = f"💬 **Ticket Update - #{ticket_id}**\n\n"
        admin_text += f"👤 **User:** {user.first_name} (@{user.username or 'N/A'})\n"
        admin_text += f"📝 **Message:** {message[:300]}{'...' if len(message) > 300 else ''}"
        
        keyboard = [
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
             InlineKeyboardButton("👁️ View Full", callback_data=f"view_{ticket_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            if message_type == 'photo' and file_id:
                await context.bot.send_photo(
                    chat_id=self.admin_group_id,
                    photo=file_id,
                    caption=admin_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await context.bot.send_message(
                    chat_id=self.admin_group_id,
                    text=admin_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            logger.error(f"Error sending admin update: {e}")

    async def reply_to_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin reply to ticket"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        ticket_id = int(query.data.split('_')[1])
        
        # Get ticket info
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            await query.edit_message_text("❌ Ticket not found.")
            return
        
        context.user_data['replying_to_ticket'] = ticket_id
        
        reply_text = f"💬 **Replying to Ticket #{ticket_id}**\n\n"
        reply_text += f"👤 **User:** {ticket[2]} (@{ticket[2] or 'N/A'})\n"
        reply_text += f"📝 **Subject:** {ticket[4]}\n\n"
        reply_text += "Type your reply message (text or photo):"
        
        await query.edit_message_text(reply_text, parse_mode=ParseMode.MARKDOWN)

    async def view_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """View full ticket details"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            # Send new message instead of editing for error cases
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text="❌ Access denied. Admin only."
            )
            return
        
        ticket_id = int(query.data.split('_')[1])
        
        # Get ticket details
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text="❌ Ticket not found."
            )
            return
        
        # Get messages
        messages = self.get_ticket_messages(ticket_id)
        
        # Format ticket details
        status_emoji = "🟢" if ticket[6] == 'open' else "🔴"
        ticket_text = f"🎫 **Ticket #{ticket[0]} {status_emoji}**\n\n"
        ticket_text += f"👤 **User:** {ticket[2]} (@{ticket[2] or 'N/A'})\n"
        ticket_text += f"📂 **Category:** {ticket[3]}\n"
        ticket_text += f"📝 **Subject:** {ticket[4]}\n"
        ticket_text += f"📅 **Created:** {ticket[8]}\n"
        ticket_text += f"📋 **Status:** {ticket[6].title()}\n\n"
        ticket_text += f"**📄 Description:**\n{ticket[5]}\n\n"
        
        # Add ALL messages (not just recent)
        if messages:
            ticket_text += "**💬 Full Conversation:**\n"
            for msg in messages:  # Show ALL messages
                sender = "🛡️ Admin" if msg[5] else "👤 User"
                timestamp = msg[6][:16]  # Show date/time (YYYY-MM-DD HH:MM)
                
                if msg[3] == 'photo':  # If it's a photo message
                    msg_content = f"[📸 Image] {msg[2] or ''}"
                else:
                    msg_content = msg[2] or "[No text]"
                
                # Keep messages readable but complete
                if len(msg_content) > 200:
                    msg_content = msg_content[:200] + "..."
                
                ticket_text += f"{timestamp} - {sender}: {msg_content}\n"
        
        # Truncate only if extremely long (increased limit)
        if len(ticket_text) > 4000:
            ticket_text = ticket_text[:3800] + "\n\n... [Message truncated - too many messages]"
        
        keyboard = [
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
             InlineKeyboardButton("🔒 Close", callback_data=f"admin_close_{ticket_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send as new message instead of editing to avoid photo/text conflicts
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=ticket_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

    async def list_open_tickets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show only open tickets"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        # Get open tickets only
        open_tickets = self.execute_query('''
            SELECT id, username, category, subject, created_at 
            FROM tickets WHERE status = 'open' 
            ORDER BY created_at DESC
        ''', fetch_all=True)
        
        tickets_text = "🟢 **Open Tickets**\n\n"
        
        if open_tickets:
            for ticket in open_tickets:
                username = ticket[1] or "Unknown"
                subject = ticket[3][:30] + "..." if len(ticket[3]) > 30 else ticket[3]
                
                # Handle datetime formatting
                if ticket[4]:
                    if hasattr(ticket[4], 'strftime'):
                        created = ticket[4].strftime("%Y-%m-%d %H:%M")
                    else:
                        created = str(ticket[4])[:16] if len(str(ticket[4])) > 16 else str(ticket[4])
                else:
                    created = "N/A"
                
                tickets_text += f"🎫 **#{ticket[0]}** - {ticket[2]}\n"
                tickets_text += f"👤 {username} | 📝 {subject}\n"
                tickets_text += f"📅 {created}\n\n"
        else:
            tickets_text += "No open tickets found.\n"
        
        # Add back button
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(tickets_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def list_closed_tickets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show only closed tickets"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        # Get closed tickets only
        closed_tickets = self.execute_query('''
            SELECT id, username, category, subject, closed_at 
            FROM tickets WHERE status = 'closed' 
            ORDER BY closed_at DESC
        ''', fetch_all=True)
        
        tickets_text = "🔴 **Closed Tickets**\n\n"
        
        if closed_tickets:
            for ticket in closed_tickets:
                username = ticket[1] or "Unknown"
                subject = ticket[3][:30] + "..." if len(ticket[3]) > 30 else ticket[3]
                
                # Handle datetime formatting
                if ticket[4]:
                    if hasattr(ticket[4], 'strftime'):
                        closed = ticket[4].strftime("%Y-%m-%d %H:%M")
                    else:
                        closed = str(ticket[4])[:16] if len(str(ticket[4])) > 16 else str(ticket[4])
                else:
                    closed = "N/A"
                
                tickets_text += f"🎫 **#{ticket[0]}** - {ticket[2]}\n"
                tickets_text += f"👤 {username} | 📝 {subject}\n"
                tickets_text += f"🔒 Closed: {closed}\n\n"
        else:
            tickets_text += "No closed tickets found.\n"
        
        # Add back button
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(tickets_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def show_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show detailed statistics"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        # Get comprehensive statistics
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        closed_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('closed',), fetch_one=True)[0]
        
        # Category statistics
        category_stats = self.execute_query('''
            SELECT category, COUNT(*) as count 
            FROM tickets GROUP BY category 
            ORDER BY count DESC
        ''', fetch_all=True)
        
        # Today's tickets
        today_tickets = self.execute_query('''
            SELECT COUNT(*) FROM tickets 
            WHERE DATE(created_at) = CURRENT_DATE
        ''', fetch_one=True)[0]
        
        stats_text = "📈 **Detailed Statistics**\n\n"
        stats_text += f"🎫 **Total Tickets:** {total_tickets}\n"
        stats_text += f"🟢 **Open:** {open_tickets}\n"
        stats_text += f"🔴 **Closed:** {closed_tickets}\n"
        stats_text += f"📅 **Today:** {today_tickets}\n\n"
        
        if category_stats:
            stats_text += "📊 **By Category:**\n"
            for category, count in category_stats:
                stats_text += f"• {category}: {count}\n"
        
        # Add back button
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def manage_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manage specific ticket - triggered by /manage_X command"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Admin only.")
            return
        
        # Extract ticket ID from command
        command_text = update.message.text
        try:
            ticket_id = int(command_text.split('_')[1])
        except (IndexError, ValueError):
            await update.message.reply_text("❌ Invalid ticket ID.")
            return
        
        # Get ticket details
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            await update.message.reply_text(f"❌ Ticket #{ticket_id} not found.")
            return
        
        # Get all messages for this ticket
        messages = self.get_ticket_messages(ticket_id)
        
        # Format ticket details
        status_emoji = "🟢" if ticket[6] == 'open' else "🔴"
        ticket_text = f"🎫 **Ticket #{ticket[0]} {status_emoji}**\n\n"
        ticket_text += f"👤 **User:** {ticket[2]} (@{ticket[2] or 'N/A'})\n"
        ticket_text += f"📂 **Category:** {ticket[3]}\n"
        ticket_text += f"📝 **Subject:** {ticket[4]}\n"
        ticket_text += f"📋 **Status:** {ticket[6].title()}\n\n"
        ticket_text += f"**📄 Description:**\n{ticket[5]}\n\n"
        
        # Add ALL messages
        if messages:
            ticket_text += "**💬 Full Conversation:**\n"
            for msg in messages:
                sender = "🛡️ Admin" if msg[5] else "👤 User"
                timestamp = msg[6]
                
                # Handle datetime formatting
                if hasattr(timestamp, 'strftime'):
                    time_str = timestamp.strftime("%H:%M")
                else:
                    time_str = str(timestamp)[11:16] if len(str(timestamp)) > 16 else str(timestamp)
                
                if msg[3] == 'photo':
                    msg_content = f"[📸 Image] {msg[2] or ''}"
                else:
                    msg_content = msg[2] or "[No text]"
                
                # Keep messages readable
                if len(msg_content) > 150:
                    msg_content = msg_content[:150] + "..."
                
                ticket_text += f"{time_str} - {sender}: {msg_content}\n"
        
        # Truncate if too long
        if len(ticket_text) > 4000:
            ticket_text = ticket_text[:3800] + "\n\n... [Message truncated]"
        
        # Create management buttons
        keyboard = []
        if ticket[6] == 'open':
            keyboard.append([
                InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
                InlineKeyboardButton("✅ Take", callback_data=f"take_{ticket_id}")
            ])
            keyboard.append([InlineKeyboardButton("🔒 Close", callback_data=f"admin_close_{ticket_id}")])
        else:
            keyboard.append([InlineKeyboardButton("📖 View Only", callback_data=f"view_{ticket_id}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_dashboard")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(ticket_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        """Go back to main dashboard"""
        query = update.callback_query
        await query.answer()
        
        # Re-run dashboard command
        await self.dashboard(update, context)
        """Admin takes ownership of ticket"""
        query = update.callback_query
        await query.answer()
        
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Access denied. Admin only.")
            return
        
        ticket_id = int(query.data.split('_')[1])
        admin_id = query.from_user.id
        admin_name = query.from_user.first_name
        
        # Update ticket assignment
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE tickets SET assigned_admin = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (admin_id, ticket_id))
        conn.commit()
        conn.close()
        
        await query.answer(f"✅ You have taken ticket #{ticket_id}")
        
        # Update the message
        original_text = query.message.text
        updated_text = original_text + f"\n\n✅ **Assigned to:** {admin_name}"
        
        keyboard = [
            [InlineKeyboardButton("💬 Reply", callback_data=f"reply_{ticket_id}"),
             InlineKeyboardButton("👁️ View", callback_data=f"view_{ticket_id}")],
            [InlineKeyboardButton("🔒 Close", callback_data=f"admin_close_{ticket_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(updated_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin dashboard"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Admin only.")
            return
        
        # Get ticket statistics using execute_query
        open_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('open',), fetch_one=True)[0]
        closed_tickets = self.execute_query('SELECT COUNT(*) FROM tickets WHERE status = ?', ('closed',), fetch_one=True)[0]
        total_tickets = self.execute_query('SELECT COUNT(*) FROM tickets', fetch_one=True)[0]
        
        # Get ALL tickets (not just recent)
        all_tickets = self.execute_query('''
            SELECT id, username, category, subject, status, created_at 
            FROM tickets ORDER BY 
                CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                created_at DESC
        ''', fetch_all=True)
        
        dashboard_text = f"📊 **Admin Dashboard**\n\n"
        dashboard_text += f"🎫 **Total Tickets:** {total_tickets}\n"
        dashboard_text += f"🟢 **Open:** {open_tickets}\n"
        dashboard_text += f"🔴 **Closed:** {closed_tickets}\n\n"
        dashboard_text += "📋 **All Tickets:**\n"
        
        if all_tickets:
            for ticket in all_tickets:
                status_emoji = "🟢" if ticket[4] == "open" else "🔴"
                username = ticket[1] or "Unknown"
                subject = ticket[3][:25] + "..." if len(ticket[3]) > 25 else ticket[3]
                
                # Handle datetime object from PostgreSQL
                if ticket[5]:
                    if hasattr(ticket[5], 'strftime'):
                        # It's a datetime object
                        created = ticket[5].strftime("%Y-%m-%d %H:%M")
                    else:
                        # It's already a string
                        created = ticket[5][:16] if len(str(ticket[5])) > 16 else str(ticket[5])
                else:
                    created = "N/A"
                    
                dashboard_text += f"{status_emoji} **#{ticket[0]}** - {ticket[2]}\n"
                dashboard_text += f"👤 {username} | 📝 {subject}\n"
                dashboard_text += f"📅 {created}\n"
                dashboard_text += f"🔗 /manage_{ticket[0]} (Click to manage)\n\n"
        else:
            dashboard_text += "No tickets found.\n"
        
        # Truncate if too long for Telegram (4096 chars limit)
        if len(dashboard_text) > 4000:
            dashboard_text = dashboard_text[:3800] + "\n\n... [List truncated - too many tickets]"
        
        keyboard = [
            [InlineKeyboardButton("🟢 Open Only", callback_data="list_open"),
             InlineKeyboardButton("🔴 Closed Only", callback_data="list_closed")],
            [InlineKeyboardButton("📈 Statistics", callback_data="stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(dashboard_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def my_tickets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user's tickets"""
        user_id = update.effective_user.id
        
        tickets = self.execute_query('''
            SELECT id, category, subject, status, created_at 
            FROM tickets WHERE user_id = ? ORDER BY created_at DESC
        ''', (user_id,), fetch_all=True)
        
        if not tickets:
            await update.message.reply_text("📋 You don't have any tickets yet. Use /ticket to create one.")
            return
        
        tickets_text = "📋 **Your Tickets:**\n\n"
        
        for ticket in tickets:
            status_emoji = "🟢" if ticket[3] == "open" else "🔴"
            tickets_text += f"{status_emoji} **#{ticket[0]}** - {ticket[1]}\n"
            tickets_text += f"📝 {ticket[2]}\n"
            tickets_text += f"📅 {ticket[4]}\n\n"
        
        await update.message.reply_text(tickets_text, parse_mode=ParseMode.MARKDOWN)

    async def close_ticket(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle ticket closing"""
        query = update.callback_query
        await query.answer()
        
        is_admin_close = query.data.startswith('admin_close_')
        ticket_id = int(query.data.split('_')[-1])
        user_id = update.effective_user.id
        
        # Verify permissions
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, status FROM tickets WHERE id = ?', (ticket_id,))
        result = cursor.fetchone()
        
        if not result:
            await query.edit_message_text("❌ Ticket not found.")
            return
        
        ticket_owner, current_status = result
        
        if not (user_id == ticket_owner or self.is_admin(user_id)):
            await query.edit_message_text("❌ You don't have permission to close this ticket.")
            return
        
        if current_status == 'closed':
            await query.edit_message_text("❌ This ticket is already closed.")
            return
        
        # Close ticket
        cursor.execute('''
            UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (ticket_id,))
        conn.commit()
        conn.close()
        
        # Clear active ticket from user data if this user closed it
        if user_id == ticket_owner and context.user_data.get('active_ticket') == ticket_id:
            context.user_data.pop('active_ticket', None)
        
        await query.edit_message_text(f"✅ Ticket #{ticket_id} has been closed successfully.")
        
        # Notify the other party
        if self.is_admin(user_id):
            # Admin closed, notify user
            try:
                await context.bot.send_message(
                    chat_id=ticket_owner,
                    text=f"🔒 Your ticket #{ticket_id} has been closed by an administrator.\n\n"
                         "If you need further assistance, feel free to create a new ticket with /ticket"
                )
            except:
                pass
        else:
            # User closed, notify admins
            try:
                await context.bot.send_message(
                    chat_id=self.admin_group_id,
                    text=f"🔒 Ticket #{ticket_id} has been closed by the user."
                )
            except:
                pass

    def run(self):
        """Run the bot"""
        application = Application.builder().token(self.token).build()
        
        # Command handlers
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("ticket", self.create_ticket))
        application.add_handler(CommandHandler("dashboard", self.dashboard))
        application.add_handler(CommandHandler("mytickets", self.my_tickets))
        
        # Dynamic ticket management commands (e.g., /manage_1, /manage_2, etc.)
        application.add_handler(MessageHandler(filters.Regex(r'^/manage_\d+
        
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
        
        # Message handlers
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
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
    bot.run()), self.manage_ticket))
        
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
        
        # Message handlers
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
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
