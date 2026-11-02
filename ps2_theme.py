"""Presentación PS2 Night. Sin red, procesos, preferencias ni lógica UDPBD."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk

ASSETS = Path(__file__).resolve().parent / 'assets'
BG = '#060e20'
CARD = '#0b1730'
TEXT = '#e5efff'
MUTED = '#99afce'
BLUE = '#87c6ff'


def install(root):
    root.configure(background=BG)
    root.option_add('*Font', '{DejaVu Sans} 10')
    root.option_add('*TCombobox*Listbox.background', '#0c1c36')
    root.option_add('*TCombobox*Listbox.foreground', TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground', '#2359a2')
    root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
    style = ttk.Style(root)
    style.theme_use('clam')
    images = {}
    for path in ASSETS.glob('*.png'):
        if path.stem != 'ps2-night':
            images[path.stem] = tk.PhotoImage(master=root, file=str(path))
    root._ps2_images = images  # Retener las imágenes mientras la ventana exista.
    style.configure('.', background=BG, foreground=TEXT, font=('DejaVu Sans', 10),
                    bordercolor='#243c60', lightcolor=CARD, darkcolor=CARD,
                    troughcolor=BG, focuscolor=BLUE)
    style.configure('TFrame', background=BG)
    style.configure('Card.TFrame', background=CARD)
    style.element_create('PS2.card', 'image', images['card'], border=24, width=48, height=48, padding=0, sticky='nsew')
    style.layout('Card.TFrame', [('PS2.card', {'sticky':'nsew'})])
    style.configure('Inner.TFrame', background=CARD)
    style.configure('TLabel', background=BG, foreground=TEXT)
    style.configure('Card.TLabel', background=CARD)
    style.configure('Section.TLabel', background=CARD, foreground=BLUE,
                    font=('DejaVu Sans', 9, 'bold'))
    style.configure('Subtitle.TLabel', background=BG, foreground=MUTED, font=('DejaVu Sans',9))
    style.configure('CardHint.TLabel', background=CARD, foreground=MUTED, font=('DejaVu Sans',9))
    style.configure('Metric.TLabel', background=CARD, foreground=TEXT, font=('DejaVu Sans',10,'bold'))
    for name, prefix in [('TButton','button'),('Action.TButton','primary')]:
        element='PS2.'+prefix
        style.element_create(element, 'image', images[prefix],
                             ('disabled', images['button-disabled']),
                             ('pressed', images[prefix+'-pressed']),
                             ('active', images[prefix+'-hover']),
                             border=16, width=32, height=32, padding=0, sticky='nsew')
        style.layout(name, [(element, {'sticky':'nsew','children':[
            ('Button.focus', {'sticky':'nsew','children':[
                ('Button.padding', {'sticky':'nsew','children':[
                    ('Button.label', {'sticky':'nsew'})]})]})]})])
        style.configure(name, padding=(18,11), foreground=TEXT, background=CARD,
                        font=('DejaVu Sans',10,'bold'), borderwidth=0, focusthickness=1)
        style.map(name, foreground=[('disabled','#637895')])
    style.configure('Action.TButton',padding=(25,13),font=('DejaVu Sans',11,'bold'))
    for name in ['TEntry','TCombobox']:
        element='PS2.'+name
        style.element_create(element,'image',images['field'],('focus',images['field-focus']),
                             border=13,width=28,height=28,padding=0,sticky='nsew')
        if name=='TEntry':
            children=[('Entry.padding',{'sticky':'nsew','children':[
                ('Entry.textarea',{'sticky':'nsew'})]})]
        else:
            children=[('Combobox.downarrow',{'side':'right','sticky':'ns'}),
                      ('Combobox.padding',{'sticky':'nsew','children':[
                          ('Combobox.textarea',{'sticky':'nsew'})]})]
        style.layout(name,[(element,{'sticky':'nsew','children':children})])
        style.configure(name,fieldbackground='#071125',background='#071125',foreground=TEXT,
                        insertcolor=TEXT,arrowsize=17,arrowcolor=BLUE,padding=(12,11),borderwidth=0)
        style.map(name,fieldbackground=[('readonly','#071125'),('disabled','#0e192d')],
                  foreground=[('disabled','#687d9b'),('readonly',TEXT)],
                  selectbackground=[('!disabled','#2359a2')],selectforeground=[('!disabled','#ffffff')])
    style.configure('TNotebook',background=CARD,borderwidth=0,tabmargins=(0,0,0,10))
    style.element_create('PS2.tab','image',images['tab'],('selected',images['tab-selected']),border=15,width=30,height=30,padding=0,sticky='nsew')
    style.layout('TNotebook.Tab',[('PS2.tab',{'sticky':'nsew','children':[
        ('Notebook.padding',{'sticky':'nsew','children':[
            ('Notebook.label',{'sticky':'nsew'})]})]})])
    style.configure('TNotebook.Tab',padding=(18,10),foreground=MUTED,font=('DejaVu Sans',9,'bold'))
    style.map('TNotebook.Tab',foreground=[('selected',TEXT),('active',BLUE)])
    style.configure('Treeview',background='#09142a',fieldbackground='#09142a',
                    foreground=TEXT,borderwidth=0,rowheight=31,font=('DejaVu Sans Mono',9))
    style.configure('Treeview.Heading',background='#122643',foreground=MUTED,
                    relief='flat',padding=(10,9),font=('DejaVu Sans',9,'bold'))
    style.map('Treeview',background=[('selected','#214d87')],foreground=[('selected','#ffffff')])
    style.map('Treeview.Heading',background=[('active','#1b365b')])
    style.configure('Vertical.TScrollbar',background='#29476c',troughcolor='#09142a',
                    borderwidth=0,arrowsize=12,arrowcolor=MUTED)
    style.map('Vertical.TScrollbar',background=[('active','#416e9d')])
    return style


class Scene(tk.Canvas):
    """Paisaje estático: sin animación, temporizadores ni consumo en segundo plano."""
    def __init__(self, root):
        super().__init__(root, background=BG, highlightthickness=0, borderwidth=0)
        self.wallpaper = tk.PhotoImage(master=root,file=str(ASSETS/'ps2-night.png'))
        self.background_id = self.create_image(0,0,image=self.wallpaper,anchor='n')
        self.create_text(34,32,text='P L A Y S T A T I O N   2   /   N E T W O R K',anchor='nw',
                         fill=BLUE,font=('DejaVu Sans',9,'bold'))
        self.create_text(32,49,text='UDPBD',anchor='nw',fill='#edf5ff',font=('DejaVu Sans',36,'bold'))
        self.create_text(35,111,text='Tus juegos. Tu consola. Sin límites de espacio.',anchor='nw',
                         fill='#a6bddc',font=('DejaVu Sans',11))
        self.shapes=[]
        # Símbolos geométricos discretos, no interactivos.
        self.shapes.append(self.create_polygon(0,0,1,1,2,2,outline='#68bcd3',fill='',width=2))
        self.shapes.append(self.create_oval(0,0,1,1,outline='#b781a7',width=2))
        self.shapes.append(self.create_line(0,0,1,1,fill='#88afe7',width=2))
        self.shapes.append(self.create_line(0,0,1,1,fill='#88afe7',width=2))
        self.shapes.append(self.create_rectangle(0,0,1,1,outline='#b4a1d5',width=2))
        self.content=ttk.Frame(self)
        self.content_id=self.create_window(32,140,window=self.content,anchor='nw')
        self.bind('<Configure>',self.resize)

    def resize(self,event):
        self.coords(self.background_id,event.width/2,-60)
        self.itemconfigure(self.content_id,width=max(1,event.width-64),height=max(1,event.height-162))
        x=event.width-187
        self.coords(self.shapes[0],x,76,x+10,58,x+20,76)
        self.coords(self.shapes[1],x+39,57,x+59,77)
        self.coords(self.shapes[2],x+80,58,x+98,76)
        self.coords(self.shapes[3],x+98,58,x+80,76)
        self.coords(self.shapes[4],x+119,58,x+137,76)


def card(parent, **kwargs):
    return ttk.Frame(parent,style='Card.TFrame',padding=14,**kwargs)


def wrapped_label(parent, **kwargs):
    label=ttk.Label(parent,**kwargs)
    parent.bind('<Configure>',lambda event:label.configure(wraplength=max(100,event.width-40)),add='+')
    return label
