from __future__ import annotations
import os, queue, subprocess, threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog,messagebox,ttk
from .architect_adapter import sibling_checkout,verify
from .core import run_final_validation,run_prefinal_validation
from .settings import load_settings,save_architect_path
from .theme import apply
class Application:
 def __init__(self,root):
  self.root=root; self.q=queue.Queue(); self.active=False; self.last_attempt=None; self.architect=tk.StringVar(); self.folder=tk.StringVar(); self.terminal=tk.StringVar(); self.status=tk.StringVar(value='○ Ready'); self.detail=tk.StringVar(value='Select an Architect repository and Stage Output folder.')
  root.title('EV4 Architect Stage QC'); root.minsize(720,480); apply(root); self._build(); root.protocol('WM_DELETE_WINDOW',self._close); self._restore(); root.after(100,self._poll)
 def _build(self):
  f=ttk.Frame(self.root,padding=16);f.grid(sticky='nsew');self.root.columnconfigure(0,weight=1); f.columnconfigure(1,weight=1)
  ttk.Label(f,text='EV4 Architect Stage QC',style='Title.TLabel').grid(column=0,row=0,columnspan=3,sticky='w',pady=(0,14))
  self._row(f,1,'Architect Repository',self.architect,self.select_arch,'Select Architect Repository'); ttk.Button(f,text='Verify Architect Connection',command=self.connection).grid(column=2,row=2,sticky='e',pady=(0,10))
  self._row(f,3,'Stage Output Folder',self.folder,self.select_folder,'Select Stage Folder'); ttk.Label(f,textvariable=self.status,style='Status.TLabel').grid(column=0,row=5,columnspan=3,sticky='w'); ttk.Label(f,textvariable=self.detail,wraplength=680).grid(column=0,row=6,columnspan=3,sticky='w',pady=(0,10))
  self.pref=ttk.Button(f,text='Run Prefinal Validation',command=self.prefinal);self.pref.grid(column=0,row=7,sticky='w'); self.open_button=ttk.Button(f,text='Open Result Folder',command=self.open_result,state='disabled');self.open_button.grid(column=1,row=7,sticky='w');
  self._row(f,8,'Project Gate Export Stage JSON',self.terminal,self.select_terminal,'Select Final Stage JSON'); self.final=ttk.Button(f,text='Run Final Validation',command=self.final_validation);self.final.grid(column=0,row=10,sticky='w')
  for child in f.winfo_children(): child.grid_configure(padx=4)
 def _row(self,f,row,label,var,command,text):
  ttk.Label(f,text=label).grid(column=0,row=row,sticky='w'); ttk.Entry(f,textvariable=var).grid(column=1,row=row,sticky='ew'); ttk.Button(f,text=text,command=command).grid(column=2,row=row,sticky='e',pady=(0,10))
 def _restore(self):
  saved=load_settings().get('architect_repository_path'); auto=sibling_checkout(); p=Path(saved) if saved else auto
  if p:self.architect.set(str(p));self.connection()
 def select_arch(self):
  p=filedialog.askdirectory(parent=self.root);
  if p:self.architect.set(p);self.connection()
 def select_folder(self):
  p=filedialog.askdirectory(parent=self.root)
  if p:self.folder.set(p);self.detail.set('Discovery complete; not yet evaluated.')
 def select_terminal(self):
  p=filedialog.askopenfilename(parent=self.root,filetypes=[('JSON files','*.json')])
  if p:self.terminal.set(p)
 def connection(self):
  c=verify(Path(self.architect.get())) if self.architect.get() else None
  if c and c.ok:
   save_architect_path(c.path);self.status.set('✓ Compatible Architect runtime')
   detail=f'Observed commit: {c.commit}\nCompatibility: Authority files verified\nAuthority files: {len(c.identities)} / {len(c.identities)} verified'
   if c.commit != c.reference_commit: detail+='\nRepository commit differs from the reference commit; locked Authority files remain compatible.'
   self.detail.set(detail)
  else:self.status.set('✕ Incompatible Architect runtime');self.detail.set(c.reason if c else 'Select an Architect repository.')
 def _start(self,fn,args):
  if self.active:return
  self.active=True;self.pref.configure(state='disabled');self.final.configure(state='disabled');self.status.set('ℹ Running validation'); self.detail.set('The operation is running; please wait.')
  threading.Thread(target=self._worker,args=(fn,args),daemon=True).start()
 def _worker(self,fn,args):
  try:self.q.put(fn(*args))
  except Exception as exc:
   from .models import CoreResult
   self.q.put(CoreResult(False,None,'INTERNAL_APPLICATION_ERROR',f'Unexpected application error: {type(exc).__name__}: {exc}','Review inputs and retry.'))
 def prefinal(self): self._start(run_prefinal_validation,(Path(self.folder.get()),Path(self.architect.get())))
 def final_validation(self): self._start(run_final_validation,(Path(self.folder.get()),Path(self.terminal.get()),Path(self.architect.get())))
 def _poll(self):
  try:
   r=self.q.get_nowait();self.active=False;self.pref.configure(state='normal');self.final.configure(state='normal');self.last_attempt=r.attempt_path;self.open_button.configure(state='normal' if r.attempt_path else 'disabled');self.status.set('✓ Validation completed successfully' if r.success else '✕ Validation failed');self.detail.set(f'{r.code}: {r.reason}\nNext action: {r.next_action}')
  except queue.Empty:pass
  self.root.after(100,self._poll)
 def open_result(self):
  if self.last_attempt:
   if os.name=='nt':os.startfile(self.last_attempt)
   else:subprocess.Popen(['xdg-open',str(self.last_attempt)])
 def _close(self):
  if self.active:messagebox.showinfo('Validation active','The current validation must finish before closing.');return
  self.root.destroy()
