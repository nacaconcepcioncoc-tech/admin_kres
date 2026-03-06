from django.urls import path
from . import views

app_name = 'pages'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('customers/', views.customers, name='customers'),
    path('inventory/', views.inventory, name='inventory'),
    path('orders/', views.orders, name='orders'),
    path('payments/', views.payments, name='payments'),
    path('reports/', views.reports, name='reports'),
    path('chatbox/', views.chatbox, name='chatbox'),
    path('features/', views.features, name='features'),
]