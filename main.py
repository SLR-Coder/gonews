def hello_world(request):
    """
    Google Cloud Function tarafından tetiklenen basit bir HTTP fonksiyonu.
    Bir istek aldığında "Merhaba GoNews Bulut!" mesajını döndürür.
    """
    print("Fonksiyon başarıyla tetiklendi!")
    return "Merhaba GoNews Bulut!"