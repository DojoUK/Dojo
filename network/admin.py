from django.contrib import admin

from .models import ClubReview, FavouriteClub, MemberAccount


@admin.register(MemberAccount)
class MemberAccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'email_verified_at', 'created_at')
    search_fields = ('name', 'user__email')


@admin.register(FavouriteClub)
class FavouriteClubAdmin(admin.ModelAdmin):
    list_display = ('account', 'organisation', 'created_at')


@admin.register(ClubReview)
class ClubReviewAdmin(admin.ModelAdmin):
    list_display = ('organisation', 'rating', 'account', 'updated_at')
    list_filter = ('rating',)
    search_fields = ('organisation__name', 'account__name', 'text')
