from django.shortcuts import render

def dashboard(request):
    return render(request, 'dashboard.html')

def customers(request):
    return render(request, 'customers.html')

def inventory(request):
    return render(request, 'inventory.html')

def orders(request):
    return render(request, 'orders.html')

def payments(request):
    return render(request, 'payments.html')

def reports(request):
    return render(request, 'reports.html')

def chatbox(request):
    return render(request, 'chatbox.html')

def features(request):
    return render(request, 'features.html')