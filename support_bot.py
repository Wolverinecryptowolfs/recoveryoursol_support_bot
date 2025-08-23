import sqlite3
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
import os

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
        self.init_database()
        
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        
        # Tickets table
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
        
        # Messages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                message TEXT,
                message_type TEXT DEFAULT 'text',
                file_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets (id)
            )
        ''')
        
        # Admins table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                role TEXT DEFAULT 'admin',
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Categories table
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
        
        cursor.executemany('''
            INSERT OR IGNORE INTO categories (name, description) VALUES (?, ?)
        ''', default_categories)
        
        # Insert main admin
        cursor.execute('''
            INSERT OR IGNORE INTO admins (user_id, username, role, added_by) 
            VALUES (?, ?, 'main_admin', ?)
        ''', (self.main_admin_id, 'Main Admin', self.main_admin_id))
        
        conn.commit()
        conn.close()

    def get_categories(self) -> List[tuple]:
        """Get all available categories"""
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        cursor.execute('SELECT name, description FROM categories ORDER BY name')
        categories = cursor.fetchall()
        conn.close()
        return categories

    def is_admin(self, user_id: int) -> bool:
        """Check if user is an admin"""
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        cursor.execute('SELECT role FROM admins WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
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
            if self.is_main_admin(user.id):
                welcome_text += "/categories - Manage categories\n"
                welcome_text += "/admins - Manage admins\n"
        
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
            "Please provide a brief subject/title for your ticket:"
        )
        
        context.user_data['expecting'] = 'subject'

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages based on context"""
        user = update.effective_user
        
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
        if context.user_data.get('expecting') == 'description':
            caption = update.message.caption or "Image attachment"
            await self.create_ticket_final(update, context, caption, update.message.photo[-1].file_id)
        else:
            await self.handle_ticket_message(update, context, message_type='photo')

    async def create_ticket_final(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                description: str, file_id: str = None):
        """Create the final ticket"""
        user = update.effective_user
        category = context.user_data.get('ticket_category')
        subject = context.user_data.get('ticket_subject')
        
        if not category or not subject:
            await update.message.reply_text("❌ Error creating ticket. Please start over with /ticket")
            return
        
        # Create ticket in database
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO tickets (user_id, username, category, subject, description, status)
            VALUES (?, ?, ?, ?, ?, 'open')
        ''', (user.id, user.username or user.first_name, category, subject, description))
        
        ticket_id = cursor.lastrowid
        
        # Add initial message
        cursor.execute('''
            INSERT INTO ticket_messages (ticket_id, user_id, username, message, message_type, file_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (ticket_id, user.id, user.username or user.first_name, description, 
              'photo' if file_id else 'text', file_id))
        
        conn.commit()
        conn.close()
        
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
            conn = sqlite3.connect('support_tickets.db')
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id FROM tickets WHERE user_id = ? AND status = 'open' 
                ORDER BY created_at DESC LIMIT 1
            ''', (user.id,))
            result = cursor.fetchone()
            conn.close()
            
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
        
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO ticket_messages (ticket_id, user_id, username, message, message_type, file_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (active_ticket, user.id, user.username or user.first_name, message_text, message_type, file_id))
        conn.commit()
        conn.close()
        
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

    async def dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin dashboard"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Access denied. Admin only.")
            return
        
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        
        # Get ticket statistics
        cursor.execute('SELECT COUNT(*) FROM tickets WHERE status = "open"')
        open_tickets = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM tickets WHERE status = "closed"')
        closed_tickets = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM tickets')
        total_tickets = cursor.fetchone()[0]
        
        # Get recent tickets
        cursor.execute('''
            SELECT id, username, category, subject, status, created_at 
            FROM tickets ORDER BY created_at DESC LIMIT 10
        ''')
        recent_tickets = cursor.fetchall()
        
        conn.close()
        
        dashboard_text = f"📊 **Admin Dashboard**\n\n"
        dashboard_text += f"🎫 **Total Tickets:** {total_tickets}\n"
        dashboard_text += f"🟢 **Open:** {open_tickets}\n"
        dashboard_text += f"🔴 **Closed:** {closed_tickets}\n\n"
        dashboard_text += "📋 **Recent Tickets:**\n"
        
        for ticket in recent_tickets:
            status_emoji = "🟢" if ticket[4] == "open" else "🔴"
            dashboard_text += f"{status_emoji} #{ticket[0]} - {ticket[2]} - {ticket[1]} - {ticket[3][:30]}...\n"
        
        keyboard = [
            [InlineKeyboardButton("🟢 Open Tickets", callback_data="list_open"),
             InlineKeyboardButton("🔴 Closed Tickets", callback_data="list_closed")],
            [InlineKeyboardButton("📈 Statistics", callback_data="stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(dashboard_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

    async def my_tickets(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user's tickets"""
        user_id = update.effective_user.id
        
        conn = sqlite3.connect('support_tickets.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, category, subject, status, created_at 
            FROM tickets WHERE user_id = ? ORDER BY created_at DESC
        ''', (user_id,))
        
        tickets = cursor.fetchall()
        conn.close()
        
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
        
        ticket_id = int(query.data.split('_')[1])
        user_id = update.effective_user.id
        
        # Verify user owns ticket or is admin
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
        
        # Clear active ticket from user data
        if context.user_data.get('active_ticket') == ticket_id:
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
        
        # Callback handlers
        application.add_handler(CallbackQueryHandler(self.category_selected, pattern=r"^cat_"))
        application.add_handler(CallbackQueryHandler(self.close_ticket, pattern=r"^close_\d+"))
        application.add_handler(CallbackQueryHandler(self.close_ticket, pattern=r"^admin_close_\d+"))
        
        # Message handlers
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        
        # Start the bot
        print("🤖 Support Bot is starting...")
        application.run_polling()

# Configuration
if __name__ == "__main__":
    # ⚠️ IMPORTANT: Replace these values with your actual data
    BOT_TOKEN = os.getenv("BOT_TOKEN")  # Get from @BotFather
    MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID"))  # Your Telegram User ID
    ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID"))  # Your admin group chat ID (negative number)
    
    # Create and run bot
    bot = SupportBot(BOT_TOKEN, MAIN_ADMIN_ID, ADMIN_GROUP_ID)
    bot.run()
