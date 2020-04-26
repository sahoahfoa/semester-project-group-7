import logging
logger = logging.getLogger(__name__)

from datetime import datetime as dt

from django.core.mail import EmailMessage
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils.timezone import make_aware

from .models import User, Account, Transaction, UserTrigger, AccountTrigger, TransactionTrigger, Notification

def get_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@receiver(user_logged_in)
def event_login(sender, user, request, **kwargs):
    trigger = UserTrigger.objects.get(user=user)
    if trigger.enabled and trigger.on_login:
        ip = get_ip(request)
        Notification(
            user=user,
            title="User account login",
            message=f"Your user account logged in from {ip}.",
            triggered_by=trigger
        ).save()

@receiver(pre_save, sender=User)
def event_user(sender, **kwargs):
    def do_notification(user, title, message):
        trigger = UserTrigger.objects.get(user=user)
        if trigger.enabled and trigger.on_change:
            Notification(
                user=user,
                title=title,
                message=message,
                triggered_by=trigger
            ).save()

    user = kwargs.get('instance', None)
    if user:
        try:
            user_old = User.objects.get(pk=user.pk)
        except User.DoesNotExist:
            return

        user_fields = [f.name for f in User._meta.get_fields()]
        diff_fields = list(filter(lambda field: getattr(user,field,None)!=getattr(user_old,field,None), user_fields))
        tracked_fields = ['username', 'first_name', 'last_name', 'email', 'phone_number']

        if 'password' in diff_fields:
            do_notification(
                user = user,
                title = "User account password changed",
                message = (
                    "Your user account password was recently changed. "
                    "If this was not you please reset your password asap!"
                )
            )
            
        elif any(field in tracked_fields for field in diff_fields):
            do_notification(
                user = user,
                title = "User profile changed",
                message = (
                    "Your user profile was recently changed. "
                    "If this was not you please reset your password asap "
                    "and verify your user profile information!"
                )
            )


@receiver(post_save, sender=Account)
def event_account(sender, **kwargs):
    account = kwargs.get('instance', None)
    triggers = AccountTrigger.objects.filter(user=account.user, account=account, enabled=True)
    for trigger in triggers:
        title, message = 'Account balance alert', ''
        if trigger.balance_gte is None:
            if account.balance <= trigger.balance_lte:
                message = f"{account} balance is less than {trigger.balance_lte}."
        elif trigger.balance_lte is None:
            if account.balance >= trigger.balance_gte:
                message = f"{account} balance is greater than {trigger.balance_gte}."
        elif trigger.balance_gte == trigger.balance_lte:
            if account.balance == trigger.balance_gte:
                message = f"{account} balance is {trigger.balance_gte}."
        elif trigger.balance_gte < trigger.balance_lte:
            if account.balance >= trigger.balance_gte and \
               account.balance <= trigger.balance_lte:
                message = f"{account} balance is between {trigger.balance_gte} and {trigger.balance_lte}."
        else:
            if account.balance >= trigger.balance_gte or \
               account.balance <= trigger.balance_lte:
                message = f"{account} balance is outside {trigger.balance_lte} and {trigger.balance_gte}."
        
        if message:
            Notification(
                user=account.user,
                title=title,
                message=message,
                triggered_by=trigger
            ).save()



@receiver(post_save, sender=Transaction)
def event_transaction(sender, **kwargs):
    transaction = kwargs.get('instance', None)
    triggers = TransactionTrigger.objects.filter(user=transaction.account.user, account=transaction.account, enabled=True)

    for trigger in triggers:
        title, message = 'Transaction alert', ''
        
        amount = None
        if transaction.amount < 0 and trigger.type_debit:
            message += 'Debit posted '
            amount = abs(transaction.amount)
        elif transaction.amount >= 0 and trigger.type_credit:
            message += 'Credit posted '
            amount = transaction.amount
        else:
            return

        posted_time = transaction.posted.time()
        if trigger.start is None and trigger.end is None:
            pass
        elif trigger.start is None:
            if posted_time <= trigger.end:
                message += f"before {trigger.end} "
        elif trigger.end is None:
            if posted_time >= trigger.start:
                message += f"after {trigger.start} "
        elif trigger.start == trigger.end:
            if posted_time == trigger.start:
                message += f"at {trigger.start} "
        elif trigger.start < trigger.end:
            logger.info(f"{trigger.start}, {trigger.end}, {transaction.posted}")
            if posted_time >= trigger.start and \
              posted_time <= trigger.end:
                message += f"between {trigger.start} and {trigger.end} "
        else:
            if posted_time >= trigger.start or \
              posted_time <= trigger.end:
                message += f"not between {trigger.end} and {trigger.start} "
        
        if trigger.amount_gte is None and trigger.amount_lte is None:
            pass
        elif trigger.amount_gte is None:
            if amount <= trigger.amount_lte:
                message += f"and the amount is less than {trigger.amount_lte} "
        elif trigger.amount_lte is None:
            if amount >= trigger.amount_gte:
                message += f"and the amount is greater than {trigger.amount_gte} "
        elif trigger.amount_gte == trigger.amount_lte:
            if amount == trigger.amount_gte:
                message += f"and the amount is {trigger.amount_gte} "
        elif trigger.amount_gte < trigger.amount_lte:
            if amount >= trigger.amount_gte and \
               amount <= trigger.amount_lte:
                message += f"and the amount is between {trigger.amount_gte} and {trigger.amount_lte} "
        else:
            if amount >= trigger.amount_gte or \
               amount <= trigger.amount_lte:
                message += f"and the amount is not between {trigger.amount_lte} and {trigger.amount_gte} "
        
        if trigger.description_value:
            if trigger.description_value in transaction.description:
                message += f"and contains {trigger.description_value} in the description"

        if message:
            Notification(
                user=transaction.account.user,
                title=title,
                message=message,
                triggered_by=trigger
            ).save()

@receiver(pre_save, sender=Notification)
def event_notification(sender, **kwargs):
    notification = kwargs.get('instance', None)
    trigger = notification.triggered_by
    trigger.trigger_count += 1
    if trigger.sms:
        logger.info("## SEND SMS ############################# ")
        logger.info(f"## {notification.title}")
        logger.info(f"## {notification.message}")
        
        notification.sms_sent = make_aware(dt.now())

    if trigger.email:
        EmailMessage(
            subject=notification.title,
            body=notification.message,
            to=[notification.user.email],
        ).send(fail_silently=False)

        notification.email_sent = make_aware(dt.now())
