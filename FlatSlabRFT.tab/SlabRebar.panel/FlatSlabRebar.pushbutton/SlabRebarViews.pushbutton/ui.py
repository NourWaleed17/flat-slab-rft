# -*- coding: utf-8 -*-
"""User input collection for Slab Rebar Views (WPF dialog)."""
from __future__ import print_function

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewPlan,
    ElementId,
    BuiltInCategory,
    FamilySymbol,
)
from System.Windows.Markup import XamlReader
from System.Windows import Thickness
from System.Windows.Controls import ListBoxItem, ComboBoxItem, CheckBox


_XAML = u"""<Window
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
    Title="Slab Rebar Views"
    Width="640" Height="760"
    WindowStartupLocation="CenterScreen"
    ResizeMode="NoResize"
    Background="#0B1220">

  <Window.Resources>
    <Style x:Key="Card" TargetType="Border">
      <Setter Property="Background" Value="#111827"/>
      <Setter Property="BorderBrush" Value="#1F3048"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="CornerRadius" Value="10"/>
      <Setter Property="Padding" Value="16"/>
      <Setter Property="Margin" Value="0,0,0,10"/>
    </Style>
    <Style x:Key="Label" TargetType="TextBlock">
      <Setter Property="Foreground" Value="#9CA3AF"/>
      <Setter Property="FontSize" Value="11"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Margin" Value="0,0,0,6"/>
      <Setter Property="FontFamily" Value="Segoe UI"/>
    </Style>
    <Style x:Key="RunBtn" TargetType="Button">
      <Setter Property="Foreground" Value="White"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="FontWeight" Value="Bold"/>
      <Setter Property="Padding" Value="28,10"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="Root" CornerRadius="8" Padding="{TemplateBinding Padding}">
              <Border.Background>
                <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                  <GradientStop Color="#0284C7" Offset="0"/>
                  <GradientStop Color="#2563EB" Offset="1"/>
                </LinearGradientBrush>
              </Border.Background>
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="Root" Property="Opacity" Value="0.9"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="CancelBtn" TargetType="Button">
      <Setter Property="Background" Value="#1F2937"/>
      <Setter Property="Foreground" Value="#E5E7EB"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="Padding" Value="22,10"/>
      <Setter Property="BorderBrush" Value="#334155"/>
      <Setter Property="BorderThickness" Value="1"/>
      <Setter Property="Cursor" Value="Hand"/>
    </Style>
  </Window.Resources>

  <Grid Margin="20">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Margin="0,0,0,12">
      <TextBlock Text="SLAB REBAR VIEWS" Foreground="#F3F4F6" FontSize="24" FontWeight="Bold"/>
      <TextBlock Text="Select views, template, and tagging options in one step."
                 Foreground="#6B7280" FontSize="12" Margin="0,4,0,0"/>
    </StackPanel>

    <ScrollViewer Grid.Row="1" VerticalScrollBarVisibility="Auto">
      <StackPanel>
        <Border Style="{StaticResource Card}">
          <StackPanel>
            <TextBlock Text="VIEWS TO CREATE" Style="{StaticResource Label}"/>
            <ListBox x:Name="lbViews"
                     Height="300"
                     SelectionMode="Single"
                     Background="#0F172A"
                     Foreground="#E5E7EB"
                     BorderBrush="#334155"
                     BorderThickness="1"
                     FontFamily="Segoe UI"
                     FontSize="13"/>
            <TextBlock Text="Tick the views you want to create."
                       Foreground="#6B7280" FontSize="11" Margin="0,6,0,0"/>
          </StackPanel>
        </Border>

        <Border Style="{StaticResource Card}">
          <StackPanel>
            <TextBlock Text="VIEW TEMPLATE" Style="{StaticResource Label}"/>
            <ComboBox x:Name="cbTemplate"
                      Background="#E5E7EB"
                      Foreground="#111827"
                      BorderBrush="#CBD5E1"
                      BorderThickness="1"
                      Padding="8,6"
                      FontSize="13"/>

            <TextBlock Text="REBAR TAG FAMILY" Style="{StaticResource Label}" Margin="0,12,0,6"/>
            <ComboBox x:Name="cbTag"
                      Background="#E5E7EB"
                      Foreground="#111827"
                      BorderBrush="#CBD5E1"
                      BorderThickness="1"
                      Padding="8,6"
                      FontSize="13"/>
          </StackPanel>
        </Border>
      </StackPanel>
    </ScrollViewer>

    <StackPanel Grid.Row="2" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,14,0,0">
      <Button x:Name="btnCancel" Content="Cancel" Style="{StaticResource CancelBtn}" Margin="0,0,10,0"/>
      <Button x:Name="btnRun" Content="Create Views" Style="{StaticResource RunBtn}"/>
    </StackPanel>
  </Grid>
</Window>"""


def _get_view_templates(doc):
    """Return list of (name, ElementId) for all view templates."""
    collector = FilteredElementCollector(doc).OfClass(ViewPlan)
    templates = []
    for v in collector:
        if v.IsTemplate:
            templates.append((v.Name, v.Id))
    templates.sort(key=lambda t: t[0])
    return templates


def _get_rebar_tag_families(doc):
    """Return list of (label, FamilySymbol) for all rebar tag families."""
    collector = (
        FilteredElementCollector(doc)
        .OfClass(FamilySymbol)
        .OfCategory(BuiltInCategory.OST_RebarTags)
    )
    tags = []
    for sym in collector:
        try:
            label = '{} : {}'.format(sym.Family.Name, sym.Name)
            tags.append((label, sym))
        except Exception:
            pass
    tags.sort(key=lambda t: t[0])
    return tags


def collect_inputs(doc, all_view_suffixes):
    """Collect view selection, template, and tag inputs.

    Returns dict or None if cancelled.
    """
    window = XamlReader.Parse(_XAML)

    lb_views = window.FindName('lbViews')
    cb_template = window.FindName('cbTemplate')
    cb_tag = window.FindName('cbTag')
    btn_run = window.FindName('btnRun')
    btn_cancel = window.FindName('btnCancel')

    view_checks = []
    for suffix in all_view_suffixes:
        cb = CheckBox()
        cb.Content = suffix
        cb.IsChecked = True
        cb.Foreground = lb_views.Foreground
        cb.Margin = Thickness(2, 2, 2, 2)
        item = ListBoxItem()
        item.Content = cb
        item.Tag = suffix
        lb_views.Items.Add(item)
        view_checks.append(cb)

    templates = _get_view_templates(doc)
    none_item = ComboBoxItem()
    none_item.Content = '<None>'
    none_item.Tag = ElementId.InvalidElementId
    cb_template.Items.Add(none_item)
    for name, tid in templates:
        item = ComboBoxItem()
        item.Content = name
        item.Tag = tid
        cb_template.Items.Add(item)
    cb_template.SelectedIndex = 0

    tags = _get_rebar_tag_families(doc)
    skip_item = ComboBoxItem()
    skip_item.Content = '<Skip tags>'
    skip_item.Tag = None
    cb_tag.Items.Add(skip_item)
    for label, sym in tags:
        item = ComboBoxItem()
        item.Content = label
        item.Tag = sym
        cb_tag.Items.Add(item)
    cb_tag.SelectedIndex = 0

    result = [None]

    def _on_run(sender, e):
        selected_suffixes = []
        for item, cb in zip(lb_views.Items, view_checks):
            try:
                if bool(cb.IsChecked):
                    selected_suffixes.append(item.Tag)
            except Exception:
                pass
        if not selected_suffixes:
            return

        t_item = cb_template.SelectedItem
        view_template_id = t_item.Tag if t_item is not None else ElementId.InvalidElementId

        tag_item = cb_tag.SelectedItem
        tag_symbol = tag_item.Tag if tag_item is not None else None

        result[0] = {
            'selected_suffixes': list(selected_suffixes),
            'view_template_id': view_template_id,
            'tag_family_symbol': tag_symbol,
        }
        window.Close()

    def _on_cancel(sender, e):
        window.Close()

    btn_run.Click += _on_run
    btn_cancel.Click += _on_cancel

    window.ShowDialog()
    return result[0]
